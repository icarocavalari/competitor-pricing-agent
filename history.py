import sqlite3

# Every run goes into one table in this file, with the date of the run as a column
DATABASE = "results/prices.db"

COLUMNS = ["competitor", "transparency", "pricing_model", "plan", "seats", "credits",
           "amount", "currency", "period_shown", "billing", "kind", "per_month", "check"]


def connect():
    connection = sqlite3.connect(DATABASE)
    # Creates the table the first time. "check" is a reserved word in SQL, so it goes in quotes.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            run_date TEXT,
            competitor TEXT,
            transparency TEXT,
            pricing_model TEXT,
            plan TEXT,
            seats TEXT,
            credits TEXT,
            amount REAL,
            currency TEXT,
            period_shown TEXT,
            billing TEXT,
            kind TEXT,
            per_month REAL,
            "check" TEXT
        )
    """)
    return connection


def save_run(run_date, rows):
    connection = connect()

    # If a competitor was already saved on this date (running the script twice), its old rows are replaced
    competitors = set()
    for row in rows:
        competitors.add(row["competitor"])
    for competitor in competitors:
        connection.execute("DELETE FROM prices WHERE run_date = ? AND competitor = ?", (run_date, competitor))

    for row in rows:
        values = [run_date]
        for column in COLUMNS:
            values.append(row.get(column))  # None (an empty value) when the row has nothing for that column
        # One ? for run_date and one for each of the 13 columns
        connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)

    connection.commit()
    connection.close()


def previous_run_date(run_date):
    connection = connect()
    result = connection.execute("SELECT MAX(run_date) FROM prices WHERE run_date < ?", (run_date,)).fetchone()
    connection.close()
    return result[0]  # None when there's no earlier run


def price_lines(run_date):
    # Each price becomes one line with competitor, plan, billing and monthly price.
    # The seats and credits texts aren't compared, because the AI words them differently on every run.
    connection = connect()
    lines = connection.execute(
        "SELECT competitor, plan, billing, per_month FROM prices WHERE run_date = ?", (run_date,)
    ).fetchall()
    connection.close()
    return set(lines)


def same_plan(line_a, line_b):
    # Same competitor, plan and billing. Only the price can be different.
    return line_a[0] == line_b[0] and line_a[1] == line_b[1] and line_a[2] == line_b[2]


def count_same_plan(lines, line):
    count = 0
    for other in lines:
        if same_plan(other, line):
            count += 1
    return count


def find_changes(old_date, new_date):
    old_lines = price_lines(old_date)
    new_lines = price_lines(new_date)

    # A line only in the old run was removed, and a line only in the new run was added
    removed = sorted(old_lines - new_lines, key=str)
    added = sorted(new_lines - old_lines, key=str)

    # Each change is (change, competitor, plan, billing, price before, price after)
    changes = []

    # When a plan has exactly one removed price and one added price, the price changed, so it becomes one line.
    # If there are more (like Apollo showing two prices for one plan), it's not clear which became which,
    # so they stay as separate removed and added lines.
    paired_old = []
    paired_new = []
    for old_line in removed:
        if count_same_plan(removed, old_line) == 1 and count_same_plan(added, old_line) == 1:
            for new_line in added:
                if same_plan(new_line, old_line):
                    changes.append(("changed", old_line[0], old_line[1], old_line[2], old_line[3], new_line[3]))
                    paired_old.append(old_line)
                    paired_new.append(new_line)

    for line in removed:
        if line not in paired_old:
            changes.append(("removed", line[0], line[1], line[2], line[3], None))
    for line in added:
        if line not in paired_new:
            changes.append(("added", line[0], line[1], line[2], None, line[3]))

    return changes


def show_price(value):
    if value is None:
        return "no price"
    return f"{value:.2f}"


def print_changes(old_date, changes):
    if len(changes) == 0:
        print(f"No changes since {old_date}")
        return

    print(f"Changes since {old_date} (prices per month):")
    for change in changes:
        kind, competitor, plan, billing, before, after = change
        if plan is None:
            plan = "-"
        if billing is None:
            billing = "-"
        if kind == "changed":
            print(f"  changed: {competitor}, {plan}, {billing}, {show_price(before)} -> {show_price(after)}")
        elif kind == "removed":
            if before is None:
                print(f"  removed: {competitor}, {plan}, {billing}, had no price")
            else:
                print(f"  removed: {competitor}, {plan}, {billing}, was {show_price(before)}")
        else:
            print(f"  added: {competitor}, {plan}, {billing}, {show_price(after)}")


def add_changes_sheet(workbook, old_date, changes):
    sheet = workbook.create_sheet("changes")
    sheet.append(["change", "competitor", "plan", "billing", "before", "after"])
    if old_date is None:
        sheet.append(["no previous run to compare with"])
    elif len(changes) == 0:
        sheet.append([f"no changes since {old_date}"])
    else:
        for change in changes:
            kind, competitor, plan, billing, before, after = change
            # An empty price on a plan that exists means the plan has no price (like "custom pricing")
            if kind != "added" and before is None:
                before = "no price"
            if kind != "removed" and after is None:
                after = "no price"
            sheet.append([kind, competitor, plan, billing, before, after])
    return sheet
