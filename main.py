#!/usr/bin/env python3
"""
MCA Prospecting Agent - Entry Point.

This file exists for backward compatibility.
The actual agent logic is in main_agent.py.

Usage:
    python main.py              # Same as python main_agent.py
    python main.py --dry-run    # Run without writing to GHL
    python main.py --states FL  # Process only Florida
"""
from main_agent import main

if __name__ == "__main__":
    main()
