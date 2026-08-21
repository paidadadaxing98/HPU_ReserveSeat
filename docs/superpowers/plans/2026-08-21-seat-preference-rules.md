# Seat Preference Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make initialization and command-line reservation preferences follow one clear library, room, seat, and learning-window model.

**Architecture:** Keep resolved human-readable location preferences compatible with the current `location_preference` fields. Add a normalized ordered rule list for command-line and future bot inputs, then let reservation candidate generation apply the most precise matching rule first. Keep browser catalog collection read-only and independent from the interactive preference prompts.

**Tech Stack:** Python 3, argparse, Playwright, pytest, JSON configuration.

---

### Task 1: Rule parsing and configuration compatibility

**Files:**
- Modify: `seat_assistant/initialization.py`
- Modify: `seat_assistant/config.py`
- Modify: `seat_assistant/preview.py`
- Modify: `seat_assistant/seat_inventory.py`
- Test: `tests/test_initialization.py`
- Test: `tests/test_config.py`
- Test: `tests/test_preview.py`

- [x] Add failing tests for `2-x-x`, `2-9-x`, `2-9-109`, `x` time placeholders, and precision ordering.
- [x] Implement parsers that validate all numeric components and preserve command order for equal precision.
- [x] Store normalized rules while preserving old location and seat preference fields.
- [x] Apply a selected rule to the current library catalog and room catalog before selecting free seats.
- [x] Run focused tests and the existing configuration/preview tests.

### Task 2: Correct interactive initialization branches

**Files:**
- Modify: `seat_assistant/initialization.py`
- Modify: `scripts/initialize_account.py`
- Test: `tests/test_initialize_account.py`

- [x] Add failing workflow tests for random, floor-random, and exact-seat branches.
- [x] Implement validation loops and make each branch ask only the inputs it needs.
- [x] Filter room choices by the selected floor before displaying them.
- [x] Keep existing configuration on blank input; floor-random mode delegates room allocation to the scheduler.
- [x] Run focused initialization tests.

### Task 3: Fully automatic catalog collection and CLI

**Files:**
- Modify: `scripts/initialize_account.py`
- Modify: `scripts/preview_reservation.py`
- Test: `tests/test_initialize_account.py`
- Test: `tests/test_commands.py`

- [x] Add failing tests for automatic library expansion and `--seat`/`--time` help and parsing.
- [x] Implement robust visible-option collection without a manual first click.
- [x] Add repeatable `--seat`, three-value `--time`, and clear `--help` examples.
- [x] Wire parsed rules into initialization and saved configuration.
- [x] Run complete tests, compile checks, help checks, and `git diff --check`.

> Real-site login, catalog collection, and reservation submission still require a manual run in an accessible campus-network environment with a valid account session; the automated suite does not claim to replace that verification.
