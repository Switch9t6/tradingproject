import os
import sys
import dotenv

dotenv.load_dotenv(override=True)
sys.path.insert(0, ".")

from main import morning_preflight_checks

print("==========================================================================")
print("             RUNNING MAIN MORNING PRE-FLIGHT CHECKS                      ")
print("==========================================================================")

bal = morning_preflight_checks(dry_run=False)
print(f"\nFinal Pre-flight Result Balance: Rs {bal:,.2f} INR")
print("==========================================================================")
