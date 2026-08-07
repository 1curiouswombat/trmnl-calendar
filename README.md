# TRMNL Weekly Calendar

A small Python renderer and TRMNL Redirect/Alias server for a `1872 x 1404` TRMNL X weekly calendar.

The current design uses:

- Roboto Serif for day headings and the left time rail
- Roboto Flex for body text, events, all-day events, and temperatures
- Climacons for weather icons
- True 4-bit grayscale PNG output for TRMNL X

## Render

From this project directory with `uv`:

```bash
uv sync
uv run render-trmnl-calendar
```

Or with an already prepared Python environment:

```bash
python3 src/trmnl_weekly_calendar/render.py
```

The main TRMNL output is a grayscale PNG with bit depth `4`, color type `0`, and 16 gray levels:

```text
outputs/trmnl_weekly_calendar_mockup_4bit_grayscale.png
```

The script also writes a continuous 8-bit grayscale preview and a 1-bit dithered comparison image.

## TRMNL Plugin Server

Use the Redirect plugin for the real calendar. Redirect lets this server return a stable image URL plus a `refresh_rate` of 900 seconds, and it lets the `filename` change only when the generated image changes. That avoids unnecessary redraws while still checking every 15 minutes.

Alias can also work if you only want TRMNL to fetch `/image.png` directly, but Redirect is a better fit for a dynamic calendar because the JSON response controls refresh cadence and cache diffing.

Install and run with `uv`:

```bash
uv sync
uv run serve-trmnl-calendar
```

Or install and run with `pip`:

```bash
python3 -m pip install -e .
serve-trmnl-calendar
```

The weekly plugin renders a rolling 5-day window starting with today as the left-most column. The month plugin renders a rolling 5-week window starting with the current week. The energy plugin compares today and yesterday across the Emporia Vue mains and circuits, with seven-day whole-home and TV-use summaries. The server exposes:

```text
GET /weekly/trmnl.json  Weekly Redirect plugin JSON
GET /weekly/image.png   Weekly PNG image for Redirect or Alias
GET /month/trmnl.json   Month Redirect plugin JSON
GET /month/image.png    Month PNG image for Redirect or Alias
GET /energy/trmnl.json  Energy Redirect plugin JSON
GET /energy/image.png   Energy PNG image for Redirect or Alias
GET /energy/calendar.png Energy PNG compatibility alias
GET /trmnl.json         Legacy weekly Redirect plugin JSON
GET /image.png          Legacy weekly PNG image
GET /healthz            Health check
```

The server keeps a rendered-image cache in memory and persists the last-good PNG plus metadata under `outputs/server-cache` by default. When a render bucket expires, `/trmnl.json` and `/image.png` return the previous image immediately while a background refresh generates the next image and fingerprint. On startup, the server loads the persisted render before accepting requests, then prewarms stale weekly, monthly, and energy renders in the background. A background prewarm loop also refreshes each render bucket shortly after it starts, so TRMNL usually hits an already-rendered image instead of triggering work itself. Set `TRMNL_RENDER_CACHE_DIR` to move the cache, `TRMNL_PERSIST_RENDER_CACHE=0` to disable the disk cache, or `TRMNL_RENDER_PREWARM_DELAY_SECONDS` to tune the post-bucket prewarm delay.

The energy plugin loads live Emporia data when `EMPORIA_USERNAME` and `EMPORIA_PASSWORD` are available to the server process. It persists normalized readings separately from rendered images and falls back to the last saved readings when Emporia is unavailable. Without configured credentials, local rendering uses deterministic sample data.

By default, the server renders the mock events. Set `TRMNL_GOG_COMMAND` to fetch live Google Calendar JSON with `gog`. The command is a template; `{start}` is the view start date and `{end}` is the exclusive view end date.

```bash
export GOG_ACCOUNT='you@example.com'
export TRMNL_GOG_CALENDARS='Work=primary,Personal=personal,Family=family'
export TRMNL_GOG_COMMAND='gog calendar events {calendar} --account {account} --from {start} --to {end} --json --no-input'
export TRMNL_PUBLIC_BASE_URL='https://your-public-host.example.com'
serve-trmnl-calendar
```

Adjust the `gog` arguments to match the CLI you use. The command template supports `{start}`, `{end}`, `{start_datetime}`, `{end_datetime}`, and `{account}` from `GOG_ACCOUNT`.
When `TRMNL_GOG_CALENDARS` is set, it is a comma-separated list of `display label=calendar id or name` entries, and the command template must include `{calendar}`.

For the local Hermes setup, use:

```bash
export TRMNL_GOG_CALENDARS='Work=primary,Personal=personal,Family=family'
export TRMNL_GOG_COMMAND='gog calendar events {calendar} --account {account} --from {start} --to {end} --all-pages --json --no-input'
```

The parser accepts common Google Calendar shapes such as top-level arrays, `items`, `events`, or `data`, with `summary`, `location`, `start.dateTime`, `start.date`, `end.dateTime`, and `end.date`.

Weekly weather is mock data unless a live provider is configured. The server supports NWS/NOAA and Open-Meteo without extra dependencies:

```bash
export TRMNL_WEATHER_PROVIDER='nws'
export TRMNL_WEATHER_LAT='YOUR_LATITUDE'
export TRMNL_WEATHER_LON='YOUR_LONGITUDE'
export TRMNL_WEATHER_USER_AGENT='trmnl-calendar (you@example.com)'
```

For NWS, you can skip the point lookup and use a known grid forecast URL:

```bash
export TRMNL_WEATHER_FORECAST_URL='https://api.weather.gov/gridpoints/OFFICE/X,Y/forecast'
```

Or use Open-Meteo daily forecasts:

```bash
export TRMNL_WEATHER_PROVIDER='open-meteo'
export TRMNL_WEATHER_LAT='YOUR_LATITUDE'
export TRMNL_WEATHER_LON='YOUR_LONGITUDE'
```

Weather data is cached for `TRMNL_WEATHER_TTL_SECONDS`, defaulting to `21600` seconds. Set `TRMNL_WEATHER_ENABLED=0` to force mock weather. The month plugin renders compact weather only for the next 7 days.
If a weekly render ever includes dates before today, those past dates are filled from Open-Meteo historical daily weather when `TRMNL_WEATHER_LAT` and `TRMNL_WEATHER_LON` are set. Set `TRMNL_WEEKLY_HISTORICAL_WEATHER=0` to disable that historical fill.

External API calls are logged to the normal service log by default. Each outbound weather HTTP request and each live `gog` calendar command writes one `external_api_call` line with provider, sanitized host/path or command, status, duration, and date range. Query strings, account names, and raw calendar ids are not logged.

```bash
journalctl -u trmnl-calendar.service -g external_api_call --since "6 hours ago" --no-pager
```

Set `TRMNL_EXTERNAL_API_LOGGING=0` to disable outbound call logging, or set `TRMNL_LOG_LEVEL=WARNING` to suppress info-level logs.

Useful environment variables:

```text
TRMNL_HOST=0.0.0.0
TRMNL_PORT=8787
TRMNL_PUBLIC_BASE_URL=https://your-public-host.example.com
TRMNL_REFRESH_SECONDS=900
TRMNL_CALENDAR_DATA_TTL_SECONDS=7200
TRMNL_TIMEZONE=America/Denver
TRMNL_IMAGE_MODE=4bit
TRMNL_RENDER_CACHE_DIR=outputs/server-cache
TRMNL_PERSIST_RENDER_CACHE=1
TRMNL_RENDER_PREWARM_DELAY_SECONDS=5
TRMNL_GOG_CALENDARS=Work=primary,Personal=personal,Family=family
TRMNL_WEATHER_PROVIDER=nws
TRMNL_WEATHER_LAT=YOUR_LATITUDE
TRMNL_WEATHER_LON=YOUR_LONGITUDE
TRMNL_WEATHER_TTL_SECONDS=21600
TRMNL_WEEKLY_HISTORICAL_WEATHER=1
TRMNL_EXTERNAL_API_LOGGING=1
TRMNL_LOG_LEVEL=INFO
EMPORIA_USERNAME=your-emporia-username
EMPORIA_PASSWORD=your-emporia-password
EMPORIA_TIMEZONE=America/Denver
EMPORIA_TOKEN_FILE=/private/state/path/emporia-tokens.json
TRMNL_ENERGY_STATE_PATH=outputs/server-cache/energy-state.json
EMPORIA_TV_CHANNEL_NAME=TV
EMPORIA_TV_ON_THRESHOLD_WATTS=40
```

`TRMNL_IMAGE_MODE=4bit` writes a packed PNG with bit depth `4` and grayscale color type `0`.
Calendar event fills are assigned from `TRMNL_GOG_CALENDARS` labels: `Work`, `Personal`, and `Family` each render with a stable gray fill.

For TRMNL Redirect, configure the plugin Web Address to:

```text
https://your-public-host.example.com/weekly/trmnl.json
https://your-public-host.example.com/month/trmnl.json
https://your-public-host.example.com/energy/trmnl.json
```

For Alias testing, configure the Image URL to:

```text
http://your-local-or-public-host:8787/image.png
```

## Structure

```text
assets/fonts/                 Vendored fonts used by the renderer
outputs/                      Generated mockups
src/trmnl_weekly_calendar/    Rendering code
```

## Notes

The mock data is still embedded in `src/trmnl_weekly_calendar/render.py` so design iterations can stay fast and visual. Live calendar parsing lives in `src/trmnl_weekly_calendar/calendar_data.py`, and the TRMNL HTTP routes live in `src/trmnl_weekly_calendar/server.py`.
