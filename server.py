#!/usr/bin/env python3
import argparse
import os
import re
import csv
import sys
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, unquote

PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

NIGHT = None
CUTOUTS_DIR = None
OUTPUT_CSV = None
ALL_CUTOUTS = []    # full sorted+indexed list
CUTOUTS = []        # subset selected for this session
SELECTED_INDICES = []
INDICES_HEADER = ""  # written verbatim to the CSV comment line
SERVER = None


def get_cutouts(directory):
    files = sorted(os.listdir(directory))
    cutouts = []
    for f in files:
        m = re.match(r"strkid(\d+)_pid(\d+)_scimref\.jpg", f)
        if m:
            cutouts.append({"streakid": m.group(1), "pid": m.group(2), "filename": f})
    for i, c in enumerate(cutouts, start=1):
        c["index"] = i
    return cutouts


def parse_indices(spec, max_index):
    """Parse either a single range (e.g. '1-10') or up to 20 comma-separated indices.

    Returns (sorted_index_list, header_str) where header_str preserves the original
    range notation when a range was given.
    """
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    ranges = [p for p in parts if "-" in p]
    singles = [p for p in parts if "-" not in p]

    if ranges and singles:
        raise ValueError("Provide either a single range (e.g. '1-10') or up to 20 individual indices, not both.")
    if len(ranges) > 1:
        raise ValueError("Only one range is allowed (e.g. '1-10').")

    if ranges:
        a, b = ranges[0].split("-", 1)
        a, b = int(a), int(b)
        if a > b:
            raise ValueError(f"Range start ({a}) must be <= range end ({b}).")
        if a < 1 or b > max_index:
            raise ValueError(f"Range out of bounds (valid: 1-{max_index}).")
        return sorted(range(a, b + 1)), f"{a}-{b}"

    if len(singles) > 20:
        raise ValueError(f"Too many individual indices ({len(singles)}); maximum is 20.")
    indices = sorted(int(p) for p in singles)
    out_of_range = [i for i in indices if i < 1 or i > max_index]
    if out_of_range:
        raise ValueError(f"Indices out of range (valid: 1-{max_index}): {out_of_range}")
    return indices, ",".join(str(i) for i in indices)


def next_csv_path(night):
    n = 1
    while os.path.exists(os.path.join(BASE_DIR, f"decisions_{night}_{n}.csv")):
        n += 1
    return os.path.join(BASE_DIR, f"decisions_{night}_{n}.csv")


def generate_html():
    cells = []
    for c in CUTOUTS:
        cell = (
            f'<td style="text-align:center;padding:4px;vertical-align:top">'
            f'<small>#{c["index"]}</small><br>'
            f'<img src="/img/{c["filename"]}" width="155" height="155"><br>'
            f'<small>{c["streakid"]}</small><br>'
            f'<label><input type="radio" name="{c["streakid"]}" value="Real"> Real</label> '
            f'<label><input type="radio" name="{c["streakid"]}" value="Bogus" checked> Bogus</label>'
            f"</td>"
        )
        cells.append(cell)

    rows = []
    for i in range(0, len(cells), 8):
        rows.append("<tr>" + "".join(cells[i : i + 8]) + "</tr>")

    return f"""<!DOCTYPE html>
<html>
<head><title>Streak Review</title></head>
<body>
<h2>Streak Cutouts Review &mdash; {NIGHT} ({len(CUTOUTS)} cutouts)</h2>
<p>Inspecting indices: {INDICES_HEADER}</p>
<form method="POST" action="/submit">
<table border="1" cellspacing="0">
{"".join(rows)}
</table>
<br>
<input type="submit" value="Submit">
</form>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            body = generate_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        elif path.startswith("/img/"):
            filename = unquote(path[5:])
            filepath = os.path.join(CUTOUTS_DIR, filename)
            if os.path.isfile(filepath):
                with open(filepath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/submit":
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length).decode()
            params = parse_qs(body)

            with open(OUTPUT_CSV, "w", newline="") as f:
                f.write(f"# inspected_indices: {INDICES_HEADER}\n")
                writer = csv.writer(f)
                writer.writerow(["streakid", "Decision"])
                for c in CUTOUTS:
                    decision = params.get(c["streakid"], ["Bogus"])[0]
                    writer.writerow([c["streakid"], decision])

            resp = (
                f"<html><body>"
                f"<p>Saved {len(CUTOUTS)} decisions to <code>{OUTPUT_CSV}</code>.</p>"
                f"<p>Server is shutting down.</p>"
                f"</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(resp)
            threading.Thread(target=SERVER.shutdown, daemon=True).start()

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress per-request logs


def main():
    global NIGHT, CUTOUTS_DIR, OUTPUT_CSV, ALL_CUTOUTS, CUTOUTS, SELECTED_INDICES, INDICES_HEADER, SERVER

    parser = argparse.ArgumentParser(
        description="Serve streak cutouts for a given night."
    )
    parser.add_argument(
        "night",
        metavar="YYYYMMDD",
        help="Night to review (e.g. 20251001)",
    )
    parser.add_argument(
        "--indices",
        metavar="SPEC",
        help="1-based cutouts to inspect: a single range (e.g. '1-100') or up to 20 individual indices (e.g. '1,5,42'). Omit to inspect all.",
    )
    args = parser.parse_args()

    if not re.fullmatch(r"\d{8}", args.night):
        print(
            f"Error: '{args.night}' is not a valid night format. Expected YYYYMMDD (e.g. 20251001).",
            file=sys.stderr,
        )
        sys.exit(1)

    NIGHT = args.night
    CUTOUTS_DIR = os.path.join(BASE_DIR, "cutouts", NIGHT)

    if not os.path.isdir(CUTOUTS_DIR):
        print(f"Error: night directory not found: {CUTOUTS_DIR}", file=sys.stderr)
        sys.exit(1)

    ALL_CUTOUTS = get_cutouts(CUTOUTS_DIR)
    if not ALL_CUTOUTS:
        print(f"Error: no cutout images found in {CUTOUTS_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.indices:
        try:
            SELECTED_INDICES, INDICES_HEADER = parse_indices(args.indices, len(ALL_CUTOUTS))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        index_set = set(SELECTED_INDICES)
        CUTOUTS = [c for c in ALL_CUTOUTS if c["index"] in index_set]
    else:
        CUTOUTS = ALL_CUTOUTS
        SELECTED_INDICES = [c["index"] for c in ALL_CUTOUTS]
        INDICES_HEADER = f"1-{len(ALL_CUTOUTS)}"

    OUTPUT_CSV = next_csv_path(NIGHT)

    url = f"http://localhost:{PORT}"
    print(f"Night:    {NIGHT}")
    print(f"Cutouts:  {len(CUTOUTS)} of {len(ALL_CUTOUTS)}")
    print(f"Indices:  {INDICES_HEADER}")
    print(f"CSV out:  {OUTPUT_CSV}")
    print(f"Server:   {url}")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    SERVER = HTTPServer(("localhost", PORT), Handler)
    SERVER.serve_forever()
    print("Done.")


if __name__ == "__main__":
    main()
