import json
import os
import sys
from collections import Counter

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from pricing_agent import COMPETITORS, Pricing, check, clean, extract

RUNS = 10

# The last full run cost about US$0.01 for 5 calls
COST_PER_CALL = 0.002

# The correct answers for the pages in tests/pages/ (saved Nov 21), checked by eye against those pages.
# Each price is written as  plan | price currency per period | billing | kind | check result
EXPECTED = {
    "salesnav": {
        "transparency": "public",
        "pricing_model": "per_seat",
        "plans": ["core", "advanced", "advanced plus"],
        "prices": [
            "core | 119.99 USD per month | monthly billing | current | ok",
            "core | 1079.88 USD per year | annual billing | current | ok",
            "advanced | 159.99 USD per month | monthly billing | current | ok",
            "advanced | 1799.88 USD per year | annual billing | current | ok",
        ],
    },
    "apollo": {
        "transparency": "public",
        "pricing_model": "seat_plus_credits",
        "plans": ["free", "basic", "professional", "organization"],
        "prices": [
            "free | free | ok",
            "basic | 49.00 USD per month | annual billing | current | ok",
            "basic | 65.00 USD per month | monthly billing | current | ok",
            "professional | 79.00 USD per month | annual billing | current | ok",
            "professional | 99.00 USD per month | monthly billing | current | ok",
            "organization | 119.00 USD | REVIEW",
            "organization | 149.00 USD | REVIEW",
        ],
    },
    "lusha": {
        "transparency": "public",
        "pricing_model": "seat_plus_credits",
        "plans": ["free", "starter", "pro", "premium", "scale"],
        "prices": [
            "free | free | ok",
            "starter | 37.45 USD per month | annual billing | current | ok",
            "starter | 49.90 USD per month | monthly billing | current | ok",
            "pro | 52.45 USD per month | annual billing | current | ok",
            "pro | 69.90 USD per month | monthly billing | current | ok",
            "premium | 299.95 USD per month | annual billing | current | ok",
            "premium | 399.90 USD per month | monthly billing | current | ok",
        ],
    },
    "zoominfo": {
        "transparency": "blocked",
        "pricing_model": "unknown",
        "plans": [],
        "prices": [],
    },
    "cognism": {
        "transparency": "contact_sales",
        "pricing_model": "seat_plus_credits",
        "plans": ["standard", "pro"],
        "prices": [],
    },
}


# Turns one extraction into simple text lines, so two runs are easy to compare
def describe(pricing, page):
    plans = []
    prices = []

    for plan in pricing.plans:
        name = plan.name.lower().strip()
        plans.append(name)

        for price in plan.prices:
            result = check(price, page, plan)
            status = result.split(",")[0]  # only "ok", "REVIEW" or "FAIL"

            # A REVIEW price goes to a human, so only the plan and amount need to be right
            if price.amount == 0:
                prices.append(f"{name} | free | {status}")
            elif status == "REVIEW":
                prices.append(f"{name} | {price.amount:.2f} {price.currency} | REVIEW")
            else:
                prices.append(f"{name} | {price.amount:.2f} {price.currency} per {price.period_shown} | "
                              f"{price.billing} billing | {price.kind} | {status}")

    return {
        "transparency": pricing.transparency,
        "pricing_model": pricing.pricing_model,
        "plans": sorted(plans),
        "prices": sorted(prices),
    }


# Lists everything in one answer that is different from the correct answer
def differences(answer, expected):
    problems = []

    if "error" in answer:
        problems.append(f"error, {answer['error']}")
        return problems

    if answer["transparency"] != expected["transparency"]:
        problems.append(f"transparency is {answer['transparency']}, should be {expected['transparency']}")

    if answer["pricing_model"] != expected["pricing_model"]:
        problems.append(f"pricing model is {answer['pricing_model']}, should be {expected['pricing_model']}")

    for plan in expected["plans"]:
        if plan not in answer["plans"]:
            problems.append(f"missing plan: {plan}")

    for plan in answer["plans"]:
        if plan not in expected["plans"]:
            problems.append(f"extra plan: {plan}")

    for price in expected["prices"]:
        if price not in answer["prices"]:
            problems.append(f"missing price: {price}")

    for price in answer["prices"]:
        if price not in expected["prices"]:
            problems.append(f"wrong price: {price}")

    return problems


def main():
    # Reading the round name
    if len(sys.argv) != 2:
        sys.exit("Give the round a name, for example: python consistency_test.py baseline")

    round_name = sys.argv[1]

    # Create the folder with the round name if it doesn't exist yet
    folder = f"tests/{round_name}"
    os.makedirs(folder, exist_ok=True)

    # Count the runs not saved yet in this round's folder. A new round needs 50 (10 runs x 5 competitors),
    # a round that stopped halfway needs fewer, and a finished round needs none (free rescore).
    missing = 0
    for key in COMPETITORS:
        for run in range(1, RUNS + 1):
            if not os.path.exists(f"{folder}/{key}_run{run:02d}.json"):
                missing += 1

    # Tell the user how many API calls will be done, how much approximately it will cost and the option to decline running the application
    if missing > 0:
        answer = input(f"This needs {missing} API calls, about US${missing * COST_PER_CALL:.2f}. Continue? (y/n) ")
        if answer.strip().lower() != "y":
            sys.exit("Stopped, nothing was spent.")

    load_dotenv()
    client = Anthropic()

    total_cost = 0
    total_correct = 0
    report = [f"Round: {round_name}"]

    for key in COMPETITORS:
        name, url = COMPETITORS[key]

        # Always the same frozen copy of the page, so any difference between runs comes from the AI.
        # pricing_agent.py overwrites debug/ on every run, so the test keeps its own copies in tests/pages/.
        with open(f"tests/pages/{key}_page.txt", encoding="utf-8") as file:
            page_text = file.read()
        page = clean(page_text)

        answers = []
        for run in range(1, RUNS + 1):
            path = f"{folder}/{key}_run{run:02d}.json"

            if os.path.exists(path):
                with open(path, encoding="utf-8") as file:
                    saved = json.load(file)
            else:
                print(f"{name}, run {run} of {RUNS}")

                # Calling the API and extracting the result of pricing and the usage of the API to calculate the costs
                try:
                    pricing, usage = extract(client, name, page_text)
                    total_cost += usage.input_tokens * 0.10 / 1_000_000
                    total_cost += usage.output_tokens * 0.50 / 1_000_000
                    saved = pricing.model_dump()
                
                # A broken answer (cut-off JSON) is saved as an error, so it counts as a wrong run and isn't paid
                # for again. Any other error, like a wrong setting, stops the test and nothing is saved.
                except ValidationError as error:
                    print(f"  Failed: {str(error)[:200]}")
                    saved = {"error": str(error)[:200]}

                # Saving the results
                with open(path, "w", encoding="utf-8") as file:
                    json.dump(saved, file, indent=2, ensure_ascii=False)

            if "error" in saved:
                answers.append({"error": "the answer came back broken"})
            else:
                answers.append(describe(Pricing.model_validate(saved), page))

        # Count how many runs gave exactly the same answer.
        # json.dumps turns each answer into one piece of text so identical answers can be counted together.
        counts = Counter()
        for answer in answers:
            counts[json.dumps(answer)] += 1

        correct_runs = 0
        for answer in answers:
            if len(differences(answer, EXPECTED[key])) == 0:
                correct_runs += 1
        total_correct += correct_runs

        report.append("")
        report.append(f"=== {name} ===")
        report.append(f"Correct runs: {correct_runs} of {RUNS}")
        report.append(f"Different answers: {len(counts)}")

        for text, count in counts.most_common():
            problems = differences(json.loads(text), EXPECTED[key])

            if len(problems) == 0:
                report.append(f"  {count} runs, correct")
            else:
                report.append(f"  {count} runs, wrong")
                for problem in problems:
                    report.append(f"    {problem}")

    report.append("")
    report.append(f"Total correct runs: {total_correct} of {RUNS * len(COMPETITORS)}")
    report.append(f"Cost of new API calls: about US${total_cost:.4f}")

    summary = "\n".join(report)
    print(summary)
    with open(f"{folder}/summary.txt", "w", encoding="utf-8") as file:
        file.write(summary + "\n")


if __name__ == "__main__":
    main()
