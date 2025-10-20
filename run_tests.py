#!/usr/bin/env python3
"""
Helper script to run tests with various options
"""

import sys
import subprocess
from pathlib import Path

def run_command(cmd):
    """Run a command and return the exit code"""
    print(f"\n🔧 Running: {' '.join(cmd)}")
    print("=" * 70)
    result = subprocess.run(cmd)
    return result.returncode

def main():
    # Get the directory of this script
    script_dir = Path(__file__).parent
    
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("""
DeltaWatch Test Runner
=====================

Usage: python run_tests.py [option]

Options:
  (no args)     Run all tests with verbose output
  --coverage    Run tests with coverage report
  --html        Run tests with HTML coverage report
  --quick       Run tests without coverage
  --failed      Re-run only failed tests
  --help        Show this help message

Examples:
  python run_tests.py
  python run_tests.py --coverage
  python run_tests.py --html
        """)
        return 0
    
    # Determine which test command to run
    if len(sys.argv) > 1:
        option = sys.argv[1]
        
        if option == "--coverage":
            cmd = ["pytest", "tests/", "-v", "--cov=deltawatch", "--cov-report=term-missing"]
        elif option == "--html":
            cmd = ["pytest", "tests/", "-v", "--cov=deltawatch", "--cov-report=html"]
            print("\n📊 HTML coverage report will be generated in: htmlcov/index.html")
        elif option == "--quick":
            cmd = ["pytest", "tests/", "-v"]
        elif option == "--failed":
            cmd = ["pytest", "tests/", "-v", "--lf"]
        else:
            print(f"Unknown option: {option}")
            print("Use --help for available options")
            return 1
    else:
        # Default: run with verbose output
        cmd = ["pytest", "tests/", "-v"]
    
    # Run the tests
    exit_code = run_command(cmd)
    
    if exit_code == 0:
        print("\n✅ All tests passed!")
    else:
        print("\n❌ Some tests failed!")
    
    return exit_code

if __name__ == "__main__":
    sys.exit(main())
