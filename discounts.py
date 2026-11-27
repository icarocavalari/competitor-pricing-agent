# The annual discount of each plan: how much cheaper per month paying yearly is than paying monthly.
# Only current prices that passed every check are used.


def find_discounts(rows):
    # (competitor, plan) -> {"monthly": price per month, "annual": price per month}
    prices = {}
    for row in rows:
        usable = row.get("check") == "ok" and row.get("kind") == "current" and row.get("per_month") is not None
        if not usable or row.get("billing") not in ("monthly", "annual"):
            continue
        key = (row["competitor"], row["plan"])
        if key not in prices:
            prices[key] = {}
        prices[key][row["billing"]] = row["per_month"]

    discounts = []
    for key in prices:
        competitor, plan = key
        both = prices[key]
        if "monthly" in both and "annual" in both and both["monthly"] > 0:
            discount = 1 - both["annual"] / both["monthly"]
            discounts.append((competitor, plan, both["monthly"], both["annual"], round(discount, 3)))
    return discounts


def print_discounts(discounts):
    print("Annual discounts:")
    if len(discounts) == 0:
        print("  none found")
    for competitor, plan, monthly, annual, discount in discounts:
        print(f"  {competitor}, {plan}: {monthly:.2f} -> {annual:.2f} per month, {discount:.1%} off")


def add_discounts_sheet(workbook, discounts):
    sheet = workbook.create_sheet("discounts")
    sheet.append(["competitor", "plan", "monthly_billing", "annual_billing", "annual_discount"])
    for discount in discounts:
        sheet.append(list(discount))

    # Prices with two decimals, the discount as a percentage
    for row in sheet.iter_rows(min_row=2):
        row[2].number_format = "0.00"
        row[3].number_format = "0.00"
        row[4].number_format = "0.0%"
    return sheet
