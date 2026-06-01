"""
MTC Backend Test Configuration
Sets up import paths for api/ and api/collectors/
"""
import sys
import os

# Add api/ to the Python path so we can import index.py and collectors
api_dir = os.path.join(os.path.dirname(__file__), '..', 'api')
sys.path.insert(0, api_dir)
