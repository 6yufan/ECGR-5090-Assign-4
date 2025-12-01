# Use the HOPE c432_log results to automatically insert XOR key-gates
# into c432.bench and generate the locked netlist c432_locked.bench.
#
# Enter the number of key-gates (e.g., 8, 16) when prompted.

import re
import random
from collections import Counter, namedtuple

Gate = namedtuple("Gate", "lhs op args")


# ---------- Generic .bench parser ----------

def parse_bench_netlist(path):
    """
    Parse a .bench file into:
      - inputs:  [list of primary input names]
      - outputs: [list of primary output names]
      - gates:   list of Gate(lhs, op, args)
    """
    inputs = []
    outputs = []
    gates = []

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("INPUT("):
                name = line[line.find("(") + 1: line.find(")")]
                inputs.append(name)
            elif line.startswith("OUTPUT("):
                name = line[line.find("(") + 1: line.find(")")]
                outputs.append(name)
            elif "=" in line:
                lhs, rhs = line.split("=")
                lhs = lhs.strip()
                rhs = rhs.strip()
                m = re.match(r"([A-Z]+)\((.*)\)", rhs)
                if not m:
                    raise ValueError(f"Cannot parse RHS: {rhs}")
                op = m.group(1)
                args_str = m.group(2).strip()
                args = [a.strip() for a in args_str.split(",") if a.strip()]
                gates.append(Gate(lhs, op, args))
    return inputs, outputs, gates


def parse_bench_nodes(path):
    """
    Parse only the node names:
      - primary inputs
      - primary outputs
      - left-hand side (LHS) of each gate
    This is used to align node names with HOPE's log.
    """
    inputs = []
    outputs = []
    nodes = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("INPUT("):
                name = line[line.find("(") + 1: line.find(")")]
                inputs.append(name)
            elif line.startswith("OUTPUT("):
                name = line[line.find("(") + 1: line.find(")")]
                outputs.append(name)
            elif "=" in line:
                lhs = line.split("=")[0].strip()
                nodes.append(lhs)
    return inputs, outputs, nodes


# ---------- Parse HOPE log and count fault detections ----------

def parse_hope_counts_filtered(log_path, valid_names):
    """
    Parse HOPE's c432_log and count how many times each node's fault
    is detected across all test patterns.

    Only nodes in valid_names (derived from the .bench netlist:
    primary inputs, internal nodes, and primary outputs) are counted.
    """
    counts = Counter()

    with open(log_path, "r", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            # Lines starting with "test" are headers for each test vector; skip them.
            if line.startswith("test"):
                continue

            # Indented lines list detected faults for that test.
            if line[0].isspace():
                s = line.strip()
                token = s.split()[0]    # e.g., "n258" or "n258->n290"
                if "->" in token:
                    token = token.split("->")[0]
                token = token.strip()
                if token in valid_names:
                    counts[token] += 1

    return counts


def choose_lock_nodes(bench_path, log_path, num_keys):
    """
    Choose nodes to be locked:
      1) Collect all internal nodes from the .bench (nodes whose LHS starts with 'n').
      2) Use the HOPE log to count the number of fault detections for each node.
      3) Sort nodes by detection count in descending order and pick the top num_keys.
    """
    inputs, outputs, nodes = parse_bench_nodes(bench_path)
    valid_names = set(inputs + outputs + nodes)
    counts = parse_hope_counts_filtered(log_path, valid_names)

    internal_nodes = [n for n in nodes if n.startswith("n")]
    # Sort by HOPE detection count (nodes not present in the log are treated as 0).
    scored = sorted(internal_nodes, key=lambda n: counts.get(n, 0), reverse=True)

    if num_keys <= len(scored):
        chosen = scored[:num_keys]
    else:
        # Should not normally happen: c432 has plenty of internal nodes.
        chosen = scored

    return chosen, counts


# ---------- Insert XOR key-gates into the netlist ----------

def insert_keys_into_netlist(orig_inputs, orig_outputs, orig_gates, lock_nodes, key_prefix="k"):
    """
    Insert XOR key-gates into the netlist.

    For each lock_nodes[i] = x:
      1) Keep the original gate: x = OP(...)
      2) Immediately after it, insert: x_locked = XOR(x, k_i)
      3) In all subsequent gates, replace every occurrence of x in the RHS
         with x_locked once x has been locked.

    For XOR-based locking, the correct key is all zeros.
    """
    key_names = [f"{key_prefix}{i}" for i in range(len(lock_nodes))]
    lock_map = {node: (f"{node}_locked", key_names[i]) for i, node in enumerate(lock_nodes)}

    new_inputs = orig_inputs + key_names
    new_outputs = list(orig_outputs)
    new_gates = []
    # Tracks which nodes have already had their XOR key-gate inserted,
    # so that downstream uses can be replaced by the locked version.
    seen_locked = set()

    for g in orig_gates:
        # Replace inputs in RHS only for nodes that are already locked.
        new_args = []
        for a in g.args:
            if a in seen_locked:
                locked_name, _ = lock_map[a]
                new_args.append(locked_name)
            else:
                new_args.append(a)

        new_gates.append(Gate(g.lhs, g.op, new_args))

        # If this gate's output is one of the nodes to be locked:
        if g.lhs in lock_map:
            locked_name, key_name = lock_map[g.lhs]
            # Insert an XOR gate right after: locked = XOR(original, key)
            new_gates.append(Gate(locked_name, "XOR", [g.lhs, key_name]))
            seen_locked.add(g.lhs)

    return new_inputs, new_outputs, new_gates, key_names


def write_bench_from_netlist(path, inputs, outputs, gates, title="Locked c432"):
    """
    Write the modified netlist back to a .bench file.
    """
    with open(path, "w") as f:
        f.write(f"# {title}\n")
        for name in inputs:
            f.write(f"INPUT({name})\n\n")
        for name in outputs:
            f.write(f"OUTPUT({name})\n\n")
        for g in gates:
            arg_str = ", ".join(g.args)
            f.write(f"{g.lhs:<10} = {g.op}({arg_str})\n")


# ---------- main ----------

def main():
    bench_path = "c432.bench"
    hope_log_path = "c432_log"
    out_path = "c432_locked.bench"

    num_keys = int(input("Enter number of key-gates to insert (e.g., 8 or 16): "))

    # 1) Select nodes to be locked
    lock_nodes, counts = choose_lock_nodes(bench_path, hope_log_path, num_keys)
    print("\n[Info] Selected lock nodes (from most to less detectable faults):")
    for n in lock_nodes:
        print(f"  {n}  (HOPE detections = {counts.get(n, 0)})")

    # 2) Read the original netlist
    orig_inputs, orig_outputs, orig_gates = parse_bench_netlist(bench_path)

    # 3) Insert XOR key-gates
    new_inputs, new_outputs, new_gates, key_names = insert_keys_into_netlist(
        orig_inputs, orig_outputs, orig_gates, lock_nodes
    )

    # 4) Write out the locked .bench file
    title = f"Locked c432 with {len(key_names)} key-gates"
    write_bench_from_netlist(out_path, new_inputs, new_outputs, new_gates, title=title)

    print(f"\n[Done] Locked bench written to {out_path}")
    print(f"[Info] Key inputs: {', '.join(key_names)}")
    print("[Info] Correct key (for XOR insertion) is all zeros, e.g., 0...0.\n")


if __name__ == "__main__":
    main()
