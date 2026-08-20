# Seat Reservation Assistant Design

## Goal

Run a local Windows assistant that reserves one suitable library seat each day, sends the room, seat, and check-in window to the student, and lets the student change or cancel plans from a phone. The student still completes every physical card-reader action.

## Boundaries

The assistant automates only the authenticated reservation website. It never emulates a card reader, claims a check-in occurred, or retries an ambiguous submission. A failed or unclear reservation is reported and requires a later retry or manual action.

## Timing Model

Each study period stores an arrival interval, departure interval, and a configurable default arrival. Reservations start at the default arrival and end at the conservative earliest intended departure, subject to the site's 0.5-4 hour limit. The user can split longer use into a later manually approved extension; version one only reminds about extension and departure.

The reservation start time is chosen so its site-defined check-in interval (30 minutes before through 15 minutes after start) includes the predicted arrival time. It cannot guarantee a one-hour arrival interval. A late-plan change is accepted only when the resulting check-in interval remains valid; otherwise the assistant cancels the uncheckable reservation rather than risking a no-show violation.

## Components

- `config`: empty credentials and user-editable period and seat preferences.
- `domain`: time calculations, plan changes, and conservative safety decisions.
- `storage`: SQLite records for daily reservations, events, overrides, and arrival samples.
- `reservation adapter`: a Playwright boundary. Dry-run is default until selectors are verified on the authenticated site.
- `scheduler`: daily next-day booking and reminder jobs, with bounded retries.
- `control API`: local authenticated JSON API and mobile-friendly HTML page for status, time changes, cancellation, and a manual check-in-time record.
- `WeCom gateway`: an outbound notification boundary and inbound text-command parser. It is disabled until the user configures an application callback or a secure tunnel.

## Primary Flow

At 19:30 the local service plans and reserves the next day's periods. It chooses the first available configured seat and stops after a conclusive success. It notifies the user of the assigned room, seat, reservation, and check-in window. On the day, it sends check-in and end-of-use reminders.

The user may issue `morning delay`, receive a request for an estimated time, or directly send `morning delay to 09:20`. The service records the override and updates the reservation only if it can safely be changed. `cancel afternoon` cancels a not-yet-started reservation. `set morning default 09:05` changes the persistent default. Recorded check-in times and explicit updated estimated times become arrival samples; cancellations do not.

## Mobile Access

The control page uses a configurable bearer token. It binds to localhost by default. The user may expose it over a secure authenticated tunnel when remote access is required; the project does not expose it publicly by default.

## Verification

Domain, command, storage, and HTTP behavior have automated tests. Website automation is integration-tested in dry-run without credentials. A real reservation requires the user's local credentials and an authenticated selector-capture test, then a manual confirmation of the result.
