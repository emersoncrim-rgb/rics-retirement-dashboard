#!/usr/bin/env python3
import subprocess
import sys

def main():
    print("Running minimal smoke test...")
    result = subprocess.run([sys.executable, "baseline.py"], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Smoke test FAILED with code {result.returncode}:\n{result.stderr}\n{result.stdout}")
        sys.exit(1)

    print("Smoke test PASSED.\nOutput:\n" + result.stdout)

if __name__ == "__main__":
    main()
