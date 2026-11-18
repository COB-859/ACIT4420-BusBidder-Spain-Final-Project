# BusBidder Spain

**ACIT4420 Problem-solving with scripting, final project**

- **Candidate number:** 859

BusBidder Spain is a simulated marketplace for bus journeys from Madrid. Five fictional bus companies offer journeys to fifteen Spanish cities. The program:

1. reads the companies, destinations, departures and fares from files and sets invalid rows aside;
2. removes the offers that break a traveller's mandatory conditions (hard constraints);
3. ranks the remaining offers with a weighted score from 0 to 100 (soft preferences);
4. explains why the top offer ranks first and which disadvantage remains;
5. simulates a booking and a cancellation. No payment data is collected.

All companies, prices, times, ratings and emissions figures are invented.

## Requirements

- Python 3.10 or later (developed and tested with Python 3.12.3)
- No third-party packages. Only the standard library is used.

## Setup

Get the project and go into its folder. There is nothing to install.

```bash
git clone https://github.com/COB-859/ACIT4420-BusBidder-Spain-Final-Project.git
cd ACIT4420-BusBidder-Spain-Final-Project
```

If you have the project as a ZIP file, unpack it and go into the unpacked folder.

Run every command from this folder (the one that holds `main.py`), because the default folders are relative. On Windows, or wherever the command is `python` instead of `python3`, replace `python3` with `python`.

## Running the program

The entry point is `main.py`.

### Guided mode

```bash
python3 main.py
```

The program asks for the destination, the travel dates, the number of passengers, the preferred departure, the mandatory conditions and a traveller profile. At the profile question, answer `custom` to type your own weights. If one weight is above 70%, the program asks whether that is intended. It shows the ranking and offers to book one of the ranked offers, after asking for confirmation. A wrong answer is asked again. Press Enter to skip an optional question, and Ctrl+C to stop.

### Commands

Check the data files and the traveller profiles:

```bash
python3 main.py validate
```

Search with a traveller profile (the example request from the project specification):

```bash
python3 main.py search --destination Malaga --date-from 2027-07-12 --date-to 2027-07-14 --depart morning --budget 50 --max-transfers 1 --changeable --profile budget
```

Search with your own weights:

```bash
python3 main.py search --destination Malaga --date-from 2027-07-12 --date-to 2027-07-14 --depart morning --budget 50 --max-transfers 1 --changeable --weights price=40,duration=25,time=15,flex=20
```

Rank the same offers under every profile:

```bash
python3 main.py compare --destination Malaga --date-from 2027-07-12 --date-to 2027-07-14 --depart morning --budget 50 --max-transfers 1 --changeable
```

Book, list and cancel:

```bash
python3 main.py book --offer CL-MAL-0007-S --passengers 1
python3 main.py bookings
python3 main.py cancel --ref BB-20261120-7K3QX9
```

Use the reference printed by `book` in the `cancel` command. A booking is saved in `state/` and takes a seat, so later searches show one seat fewer on that bus. Delete the `state` folder to start again with no bookings.

### What the program tells the traveller

Every action ends by saying whether it worked, what changed and what to do next:

| Action | Feedback |
|---|---|
| Search | The ranking and the explanation, then the exact command to book the top offer. A bus that could not take the party twice gets the note `only 1 seat left on this bus`: one passenger is warned at the last seat, a party of four below eight seats. When the first two offers are less than 1 point apart, a note says that a small change in the weights can change the winner. |
| Search with no result | What is wrong with the offers and the value that would work, for example `47 of the 47 offers cost more than your budget of 20.00 EUR. The cheapest offer that meets your other requirements costs 29.98 EUR.` The value is taken from the offers that fail only that requirement, so it is one the traveller can act on. When no offer meets the other requirements either, the program says that one change is not enough. When the dates have no buses, it names the days that do: `BusBidder has buses from 10 July 2027 to 16 July 2027.` |
| Book | `Booking confirmed: 2 flex seats to Malaga for 95.48 EUR.`, the reference, and the seats left on that bus, or `This bus is now sold out.` |
| Book a full bus | `Not booked: this bus is sold out (SolRuta bus to Valencia, Sat 10 Jul 13:15).`, or how many seats are left. A full bus is not a mistake by the traveller, so the line does not start with `Error`. |
| Cancel | `Booking BB-... is cancelled: 2 flex seats to Malaga.`, then `Refund: 85.93 EUR (90% of the 95.48 EUR paid).` or `No refund, because basic tickets are not refundable.` |
| Bookings | The list with the refund of each cancelled booking, a count of confirmed and cancelled bookings, and how to cancel. |
| A mistake on the command line | One line with the reason and the command that shows the options. |

### Options of `search` and `compare`

| Option | Meaning |
|---|---|
| `--destination` | City name or code, for example `Malaga` or `MAL`. Capitals and accents do not matter, so `málaga` works. Required. |
| `--date-from`, `--date-to` | First and last travel date, both included, written as `2027-07-12`. Required. The data covers 10 to 16 July 2027. |
| `--depart` | Preferred departure: `morning` (06:00 to 12:00), `afternoon` (12:00 to 18:00), `evening` (18:00 to 22:00), `night` (22:00 to 06:00) or a time such as `08:30`. Required. |
| `--passengers` | Number of passengers. Default 1. |
| `--budget` | Mandatory: highest price per person in EUR. |
| `--max-transfers` | Mandatory: highest number of transfers. |
| `--max-duration` | Mandatory: longest journey in minutes, 1 or more. |
| `--changeable`, `--refundable` | Mandatory: the ticket must be changeable or refundable. |
| `--min-luggage` | Mandatory: lowest luggage allowance in kg. |
| `--allow-dominant` | Accept weights where one criterion counts for more than 70%. Without it such weights are refused, so one criterion cannot dominate by accident. |
| `--save FILE` | Also write the result to a file. |
| `--profile KEY` | `search` only. One of `budget`, `business`, `flexible`, `eco` (from `data/profiles.json`). |
| `--weights TEXT` | `search` only, instead of `--profile`. For example `price=40,duration=25,time=15,flex=20`. A percent sign is allowed: `price=40%`. |
| `--top N` | `search` only. Show only the first N ranked offers. By default, or with `--top 0`, every ranked offer is shown. |
| `--show-removed` | `search` only. List every removed offer with the reasons. |
| `--profiles KEY ...` | `compare` only. The profiles to compare. Default: all. |

### Folder options (every command)

| Option | Default | Meaning |
|---|---|---|
| `--data` | `data` | Folder with the input files. |
| `--state` | `state` | Folder where bookings are saved (`bookings.json`). Created when the first booking is made. |
| `--logs` | `logs` | Folder for the log file (`busbidder.log`). |
| `--output` | `output` | Folder for `rejected_records.txt`. |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | The command ran. A search that finds no offer is also 0. |
| 1 | An error was reported, for example a missing data file, an unknown destination, weights that do not total 100, or no seats left. An unexpected fault in the program also gives 1, with the details in the log file. |
| 2 | The command line could not be understood, for example a missing option or `--passengers two`. The program prints one line with the reason and the command that shows the options. |

## Running the tests

```bash
python3 -m unittest discover -s tests -t . -v
```

The tests use only the standard library (`unittest`). They write to temporary folders and do not change the project's files.

### Exploratory tests

The unit tests check known answers. `tools/exploratory_tests.py` looks for what they did not think of, by using the program as a traveller would. It reads the real data files and writes bookings and logs only to a temporary folder.

```bash
python3 tools/exploratory_tests.py edge
python3 tools/exploratory_tests.py rules
python3 tools/exploratory_tests.py race
python3 tools/exploratory_tests.py trials
```

| Part | What it does |
|---|---|
| `edge` | Runs 75 unusual commands, such as a date that does not exist, 1000 passengers or a booking on a full bus, and prints the exit code and the feedback for each. |
| `rules` | Runs 300 random searches, 600 random bookings and cancellations and 1,500 random command lines, and checks rules that must hold for any input, for example that every ranked offer meets every mandatory condition and that a bus never has more seats booked than it has. |
| `race` | Starts 8 bookings at the same moment for a bus with 2 seats, 20 times, and compares what the travellers were told with what was saved. |

| `trials` | Measures how stable and how even-handed the ranking is: how much each criterion really decides in the specification's example, which company each profile recommends over 60 requests (15 cities with 4 departure periods), and how often the first two offers are less than 1 point apart. |

`edge`, `rules` and `trials` print the same result on every run. `race` depends on timing, so its result can differ between runs. The command ends with exit code 1 when `edge` finds a crash or `rules` finds a broken rule.

## How offers are filtered and ranked

**Mandatory conditions** remove an offer. Enough seats for the party is always checked. The budget and the other conditions in the options table apply only when the traveller sets them. The destination and the travel dates are mandatory too: they decide which offers are looked at, and are listed with the other conditions. Every search prints the mandatory conditions, how many offers each one removed, and the preferences, as two separate lists.

**Preferences** are scored. Each criterion gives 0 to 100 points, 100 being best:

| Criterion | Weight name | Points |
|---|---|---|
| Price | `price` | Compared with the other offers left: the cheapest gets 100, the dearest 0. A range under 5 EUR is not stretched further. |
| Duration | `duration` | Compared with the other offers left, with a minimum range of 30 minutes. |
| Departure convenience | `time` | 100 inside the preferred time band, falling to 0 six hours outside it. |
| Flexibility | `flex` | 50 for a changeable ticket plus 50 for a refundable one. |
| Transfers | `transfers` | Direct 100, one transfer 50, more 0. Optional. |
| Rating | `rating` | The company's rating from 1 to 5 on a fixed scale. Optional. |
| Emissions | `emissions` | Compared with the other offers left. With a minimum range of 1 kg. A missing value gets the median of the points of the offers that report one, and is marked "estimated". If no offer reports a value, every offer gets 50. Optional. |

Total = sum of (weight / 100) x points, so the total is also 0 to 100. Weights are percentages that total 100, or fractions that total 1.0. The four criteria `price`, `duration`, `time` and `flex` must each have a weight above 0. Equal totals are ordered by lower price, then earlier departure, then offer ID.

## Project structure

| File | Purpose |
|---|---|
| `main.py` | Entry point. Calls `busbidder.cli.main()`. |
| `busbidder/exceptions.py` | The custom exceptions. All are a kind of `BusBidderError`. |
| `busbidder/validation.py` | Regular expressions for identifiers, dates and times, and parsers that turn text into checked values. |
| `busbidder/models.py` | Domain classes: `Company`, `Destination`, `Departure`, `Fare`, `Offer`, `TimeBand`, `TravelRequest`, `TravellerProfile`, `Booking`. |
| `busbidder/loader.py` | Reads the CSV and JSON files, builds the objects, sets bad rows aside. |
| `busbidder/booking.py` | Saved bookings, the seat ledger, booking and cancellation. |
| `busbidder/constraints.py` | Hard constraints in a decorator registry, and the filter. |
| `busbidder/criteria.py` | Scoring criteria in a decorator registry. |
| `busbidder/ranking.py` | Weight checks, total score, ordering and the tie rule. |
| `busbidder/explain.py` | The generated explanation of the top offer. |
| `busbidder/display.py` | The text of tables, comparisons and confirmations. Prints nothing. |
| `busbidder/logsetup.py` | Log file configuration. |
| `busbidder/cli.py` | Commands, guided mode, exit codes. The only module that prints. |
| `tools/generate_data.py` | Generator for the four main data files. Fixed seed. |
| `tools/exploratory_tests.py` | Exploratory tests: unusual input, rules that must always hold, bookings at the same moment, and trials of the ranking. |
| `tests/` | Unit tests, one file per module, one for the data generator and one for the feedback to the traveller (`test_feedback.py`), and `helpers.py` with small builders. |

## Input files (in `data/`)

| File | Content |
|---|---|
| `companies.csv` | `company_code`, `name`, `rating` (1 to 5) |
| `destinations.csv` | `destination_code`, `name`, `distance_km` |
| `departures.csv` | `departure_id`, `company_code`, `destination_code`, `departure`, `arrival` (written as `2027-07-12T08:30`), `transfers`, `seats`, `emissions_kg` (may be empty) |
| `fares.csv` | `offer_id`, `departure_id`, `fare_type` (`basic`, `standard`, `flex`), `price_eur`, `changeable`, `refundable` (`yes` or `no`), `change_fee_eur`, `refund_percent`, `luggage_kg` |
| `companies_invalid.csv`, `departures_invalid.csv`, `fares_invalid.csv` | 26 hand-written invalid rows, each with a different error. They are read on every run so the rejection path is always used. Rules without an example row here, such as the 500 EUR price limit, are covered by the unit tests. |
| `profiles.json` | The traveller profiles and their weights. |

A departure ID looks like `SR-MAL-0001` (company code, destination code, serial). An offer ID adds the fare letter: `SR-MAL-0001-B` (`B` basic, `S` standard, `F` flex). A booking reference looks like `BB-20261120-7K3QX9`.

A price above 500 EUR is rejected as implausible, and so is a ticket that cannot be changed but has a change fee. `validate` ends with exit code 1 when a row of a main data file is rejected or a profile is invalid; the rows in the three files of invalid examples are expected to be rejected and do not count.

Invalid rows are rejected and set aside, never corrected. They are listed in `output/rejected_records.txt` with file, line and reason, and in the log. A departure with zero seats is a valid record: it is loaded, logged as sold out and removed from searches by the seat condition.

To write the four main data files again (the result is identical on every run):

```bash
python3 tools/generate_data.py
```

## Files the program writes

| File | Content |
|---|---|
| `output/rejected_records.txt` | The rows set aside in the last run. Rewritten on every run. |
| `logs/busbidder.log` | Commands, load counts, rejected rows, sold-out departures, searches, bookings, cancellations and errors. |
| `state/bookings.json` | The saved bookings, cancelled ones included. The data files are never changed: seats left are the seats in `departures.csv` minus the seats of active bookings. The file is checked when it is read, so a hand-edited value that is not valid stops the command with an error. |

The program is made for one user at a time. Two copies of the program that book at the same moment can overwrite each other's booking.

## Example output (in `output/`)

These files were produced with no saved bookings. With `state/` empty or absent, the commands below produce them again. If you have made a booking, delete the `state` folder first, or add `--state` with the name of an empty folder to each command.

| File | Command |
|---|---|
| `validate.txt` | `python3 main.py validate > output/validate.txt` |
| `search_spec_example.txt` | `python3 main.py search --destination Malaga --date-from 2027-07-12 --date-to 2027-07-14 --depart morning --budget 50 --max-transfers 1 --changeable --weights price=40,duration=25,time=15,flex=20 --save output/search_spec_example.txt` |
| `compare_malaga.txt` | `python3 main.py compare --destination Malaga --date-from 2027-07-12 --date-to 2027-07-14 --depart morning --budget 50 --max-transfers 1 --changeable --save output/compare_malaga.txt` |
| `compare_barcelona.txt` | `python3 main.py compare --destination Barcelona --date-from 2027-07-12 --date-to 2027-07-14 --depart afternoon --budget 70 --save output/compare_barcelona.txt` |
| `compare_santiago.txt` | `python3 main.py compare --destination "Santiago de Compostela" --date-from 2027-07-10 --date-to 2027-07-16 --depart night --budget 60 --passengers 2 --save output/compare_santiago.txt` |
| `sensitivity_price_30.txt` | The `search_spec_example` command with `--weights price=30,duration=30,time=20,flex=20 --top 3` |
| `sensitivity_price_50.txt` | The `search_spec_example` command with `--weights price=50,duration=20,time=10,flex=20 --top 3` |
| `log_extract.txt` | Part of `logs/busbidder.log` from the runs above. |
| `rejected_records.txt` | Written by every run. |
| `exploratory_edge_cases.txt` | `python3 tools/exploratory_tests.py edge > output/exploratory_edge_cases.txt` |
| `exploratory_rules.txt` | `python3 tools/exploratory_tests.py rules > output/exploratory_rules.txt` |
| `exploratory_trials.txt` | `python3 tools/exploratory_tests.py trials > output/exploratory_trials.txt` |
| `exploratory_simultaneous_bookings.txt` | `python3 tools/exploratory_tests.py race > output/exploratory_simultaneous_bookings.txt`. The result depends on timing. |
