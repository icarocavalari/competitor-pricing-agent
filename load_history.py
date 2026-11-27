import glob
import os

from openpyxl import load_workbook

from history import find_changes, print_changes, save_run

# Run once. Loads every past run (results/<date>/prices.xlsx) into the database,
# then shows what changed between each run and the one before it.
# Running it again is safe, because each date's rows are replaced, not added twice.

run_dates = []
for path in sorted(glob.glob("results/*/prices.xlsx")):
    run_date = os.path.basename(os.path.dirname(path))  # the folder name is the date

    sheet = load_workbook(path).active
    all_rows = list(sheet.iter_rows(values_only=True))
    header = all_rows[0]
    rows = []
    for values in all_rows[1:]:
        rows.append(dict(zip(header, values)))  # {"competitor": "Lusha", "plan": "Pro", ...}

    save_run(run_date, rows)
    run_dates.append(run_date)
    print(f"Loaded {run_date}: {len(rows)} rows")

for i in range(1, len(run_dates)):
    print(f"\n--- {run_dates[i]} ---")
    changes = find_changes(run_dates[i - 1], run_dates[i])
    print_changes(run_dates[i - 1], changes)
