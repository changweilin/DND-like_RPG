import os
import sys

if __name__ == "__main__":
    import subprocess
    # Pass the project root via PYTHONPATH so the Streamlit subprocess can find engine, ai, etc.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
    subprocess.run([sys.executable, "-m", "streamlit", "run", "ui/app.py"], env=env)
