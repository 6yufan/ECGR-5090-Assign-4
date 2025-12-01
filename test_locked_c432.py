# This script performs functional validation and security evaluation
# for the locked c432 circuit generated from insert_keys_c432.py
#
# It compares:
#   1) The original c432.bench
#   2) The locked c432_locked.bench
#
# Test objectives:
#   • Verify that the locked design behaves exactly the same as the
#     original design under the correct key.
#   • Evaluate the corruption rate (pattern mismatch & bit-flip rate)
#     under several wrong key combinations.
#
# Requirements:
#     c432.bench
#     c432_locked.bench

import re
import random
from collections import namedtuple

Gate = namedtuple("Gate", "lhs op args")


# ================================================================
#                     BENCH FILE PARSER
# ================================================================

def parse_bench_netlist(path):
    """
    Parse a .bench file into:
        inputs  – list of primary input names
        outputs – list of primary output names
        gates   – list of Gate(lhs, op, args)
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
                name = line[line.find("(") + 1 : line.find(")")]
                inputs.append(name)

            elif line.startswith("OUTPUT("):
                name = line[line.find("(") + 1 : line.find(")")]
                outputs.append(name)

            elif "=" in line:
                lhs, rhs = line.split("=")
                lhs = lhs.strip()
                rhs = rhs.strip()

                m = re.match(r"([A-Z]+)\((.*)\)", rhs)
                if not m:
                    raise ValueError(f"Cannot parse RHS: {rhs}")

                op = m.group(1)
                args = [a.strip() for a in m.group(2).split(",") if a.strip()]
                gates.append(Gate(lhs, op, args))

    return inputs, outputs, gates


# ================================================================
#                     LOGIC GATE EVALUATION
# ================================================================

def eval_gate(op, arg_vals):
    """Evaluate the logic operation 'op' with input arg_vals."""
    if op == "AND":
        v = 1
        for a in arg_vals: v &= a
        return v

    if op == "OR":
        v = 0
        for a in arg_vals: v |= a
        return v

    if op == "NAND":
        v = 1
        for a in arg_vals: v &= a
        return 1 - v

    if op == "NOR":
        v = 0
        for a in arg_vals: v |= a
        return 1 - v

    if op == "NOT":
        return 1 - arg_vals[0]

    if op == "BUF":
        return arg_vals[0]

    if op == "XOR":
        v = 0
        for a in arg_vals: v ^= a
        return v

    if op == "XNOR":
        v = 0
        for a in arg_vals: v ^= a
        return 1 - v

    raise ValueError(f"Unknown gate type: {op}")


def simulate_bench(inputs, outputs, gates, input_vector):
    """
    Simulate the .bench netlist with the given input vector.

    input_vector: dict {name : 0/1} for all INPUT nodes.
    Return: output values in the same order as 'outputs'.
    """
    values = {}

    # Assign primary inputs
    for name in inputs:
        if name not in input_vector:
            raise KeyError(f"Missing input value for {name}")
        values[name] = input_vector[name]

    # Evaluate gates in listed order (bench uses topological order)
    for g in gates:
        arg_vals = [values[a] for a in g.args]
        values[g.lhs] = eval_gate(g.op, arg_vals)

    return [values[name] for name in outputs]


# ================================================================
#                RANDOM INPUT GENERATION & KEY MERGE
# ================================================================

def random_logic_input_vector(logic_inputs):
    """Generate a random binary vector for logic-only inputs."""
    return {name: random.randint(0, 1) for name in logic_inputs}


def make_locked_input_vector(locked_inputs, logic_vec, key_bits, key_names):
    """
    Construct a full input vector for the locked circuit by merging:
        • logic inputs
        • key bits
    """
    vec = {}
    for name in locked_inputs:
        if name in logic_vec:
            vec[name] = logic_vec[name]
        elif name in key_names:
            idx = key_names.index(name)
            vec[name] = int(key_bits[idx])
        else:
            raise KeyError(f"Unknown input node {name}")

    return vec


# ================================================================
#             COMPARISON: ORIGINAL vs LOCKED CIRCUIT
# ================================================================

def compare_for_key(orig_inputs, orig_outputs, orig_gates,
                    locked_inputs, locked_outputs, locked_gates,
                    logic_inputs, key_names, key_bits,
                    num_patterns=1000):
    """
    Compare original vs locked circuit under a given key.
    Generates 'num_patterns' random input patterns and computes:

    Returns:
        pattern_mismatch_rate  – fraction of patterns with mismatching outputs
        bit_flip_rate          – fraction of corrupted output bits
    """
    mismatches = 0
    total_bits = 0
    flipped_bits = 0

    for _ in range(num_patterns):
        lv = random_logic_input_vector(logic_inputs)
        gold = simulate_bench(orig_inputs, orig_outputs, orig_gates, lv)

        locked_vec = make_locked_input_vector(
            locked_inputs, lv, key_bits, key_names
        )
        locked_out = simulate_bench(locked_inputs, locked_outputs, locked_gates, locked_vec)

        if gold != locked_out:
            mismatches += 1

        for gb, lb in zip(gold, locked_out):
            total_bits += 1
            if gb != lb:
                flipped_bits += 1

    return mismatches / num_patterns, (flipped_bits / total_bits)


# ================================================================
#                          MAIN TEST FLOW
# ================================================================

def main():
    orig_path = "c432.bench"
    locked_path = "c432_locked.bench"

    # Load both circuits
    orig_inputs, orig_outputs, orig_gates = parse_bench_netlist(orig_path)
    locked_inputs, locked_outputs, locked_gates = parse_bench_netlist(locked_path)

    key_names = [n for n in locked_inputs if n.startswith("k")]
    logic_inputs = [n for n in locked_inputs if not n.startswith("k")]

    print("[Info] Original inputs:", orig_inputs)
    print("[Info] Locked inputs  :", locked_inputs)
    print("[Info] Key inputs     :", key_names)
    print("[Info] Outputs        :", orig_outputs)
    print()

    # ===============================================================
    #        1. Functional Equivalence Test (Correct Key)
    # ===============================================================

    correct_key = "0" * len(key_names)
    print(f"[Test] Verifying functional equivalence under correct key = {correct_key}")

    mismatch_found = False
    for _ in range(500):
        lv = random_logic_input_vector(logic_inputs)
        out_orig = simulate_bench(orig_inputs, orig_outputs, orig_gates, lv)
        locked_vec = make_locked_input_vector(locked_inputs, lv, correct_key, key_names)
        out_locked = simulate_bench(locked_inputs, locked_outputs, locked_gates, locked_vec)

        if out_orig != out_locked:
            mismatch_found = True
            break

    if mismatch_found:
        print("[FAILED] Locked circuit is NOT equivalent under the correct key.")
    else:
        print("[PASSED] Locked circuit is functionally equivalent (500 random patterns).")
    print()

    # ===============================================================
    #        2. Wrong Key Evaluation
    # ===============================================================

    wrong_keys = [
        "1" * len(key_names),                          # all-1 key
        "1010101010101010"[:len(key_names)],           # alternating pattern
        "00010000".zfill(len(key_names)),              # single-bit error
    ]

    num_patterns = 1000

    for wk in wrong_keys:
        if wk == correct_key:
            continue

        pmr, bfr = compare_for_key(
            orig_inputs, orig_outputs, orig_gates,
            locked_inputs, locked_outputs, locked_gates,
            logic_inputs, key_names, wk, num_patterns=num_patterns
        )

        print(f"[Wrong Key] key = {wk}")
        print(f"  Pattern mismatch rate = {pmr:.3f}")
        print(f"  Bit-flip rate         = {bfr:.3f}\n")


if __name__ == "__main__":
    main()
