#!/usr/bin/env python3
"""Saves the questions visitors type in the chat, grouped by chat session,
in a SQLite file (chat_questions.db). Nothing else is saved: no answers, no
names, no IP addresses. Phone numbers, e-mail addresses and long ID or card
numbers are replaced before saving.

Export to a file for Excel:

    python3 chatbot/question_log.py --export questions.csv
"""

import argparse
import csv
import datetime
import os
import re
import sqlite3
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(os.path.dirname(HERE), "chat_questions.db")

MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[e-mail]"),
    (re.compile(r"(?<!\d)(\+?389|0)[\s/-]?7\d([\s/-]?\d){6}(?!\d)"), "[телефон]"),
    (re.compile(r"(?<!\d)\d(?:[\s-]?\d){12,18}(?!\d)"), "[број]"),  # ЕМБГ, card numbers
]


def mask(text):
    for pattern, replacement in MASKS:
        text = pattern.sub(replacement, text)
    return text


class QuestionLog:
    def __init__(self, path=DEFAULT_DB):
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS questions (
                             id INTEGER PRIMARY KEY,
                             session TEXT NOT NULL,
                             asked_at TEXT NOT NULL,
                             question TEXT NOT NULL)""")
        self.db.commit()

    def add(self, session, question):
        at = datetime.datetime.now().isoformat(timespec="seconds")
        with self.lock:
            self.db.execute("INSERT INTO questions (session, asked_at, question) VALUES (?, ?, ?)",
                            (session, at, mask(question)))
            self.db.commit()

    def rows(self):
        """All questions, each session's together, oldest session first."""
        with self.lock:
            return self.db.execute("""
                SELECT session, asked_at, question FROM questions
                ORDER BY (SELECT MIN(id) FROM questions q WHERE q.session = questions.session), id
            """).fetchall()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export the saved chat questions.")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--export", metavar="FILE.csv", required=True,
                    help="write all questions to this CSV file (opens in Excel)")
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        print(f"No questions saved yet ({args.db} does not exist).", file=sys.stderr)
        return 1
    rows = QuestionLog(args.db).rows()
    with open(args.export, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["session", "time", "question"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} questions from {len({r[0] for r in rows})} chat sessions to {args.export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
