#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to get the size used by all resources of media in a given channel or in whole catalog if no channel is specified.
The API key used to run this script must have the permission to access resources tab and all media.

To use this script clone MediaServer client and put this file in a sub dir in the client dir.
git clone https://github.com/UbiCastTeam/mediaserver-client
mkdir examples
mv "this file" mediaserver-client/examples
"""
import csv
from datetime import datetime
import os
import sqlite3
from dotenv import load_dotenv
from ms_client.client import MediaServerClient
import argparse

from aristote import request_enrichment

load_dotenv(".env")

DATABASE_URL = os.environ["DATABASE_URL"]
CONFIG_FILE = os.environ["CONFIG_FILE"]


def get_channel_videos(msc, oid, info=None):
    if info is None:
        info = dict(channels=0, video_oids=[])
    print("//// Channel %s" % oid)
    print("Making request on channels/content/ (parent_oid=%s)" % oid)
    response = msc.api("channels/content/", params=dict(parent_oid=oid, content="cvlp"))
    if response.get("channels"):
        for item in response["channels"]:
            info["channels"] += 1
            get_channel_videos(msc, item["oid"], info)
    if response.get("videos"):
        for item in response["videos"]:
            print("// Media %s" % item["oid"])
            info["video_oids"].append(
                dict(oid=item["oid"], parent_oid=oid, type=item["type"])
            )
    return info


def print_table():
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM enrichment_requests")
    rows = cursor.fetchall()

    column_names = [description[0] for description in cursor.description]
    print(column_names)

    for row in rows:
        print(row)


def initiate_database():
    cursor = conn.cursor()

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS enrichment_requests (
        oid TEXT PRIMARY KEY,
        enrichment_id TEXT,
        request_sent_at DATETIME,
        enrichment_notification_received_at DATETIME,
        language TEXT,
        status TEXT
    )
    """
    )

    conn.commit()


def add_line(oid: str, enrichment_id: str, language: str):
    cursor = conn.cursor()

    request_sent_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "PENDING"

    cursor.execute(
        """
        INSERT INTO enrichment_requests (oid, enrichment_id, request_sent_at, language, status)
        VALUES (?, ?, ?, ?, ?)
    """,
        (oid, enrichment_id, request_sent_at, language, status),
    )

    conn.commit()
    print(f"Enrichment request with oid: {oid} has been added.")


def delete_line(oid: str):
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM enrichment_requests WHERE oid = ?
        """,
        (oid,),
    )

    conn.commit()

    print(f"Enrichment request with oid: {oid} has been deleted.")


def oid_exists(oid):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM enrichment_requests WHERE oid = ?", (oid,))

    row = cursor.fetchone()

    if row:
        return True
    return False


def get_channel_language(channel_oid: str) -> str:
    with open("channels.csv", mode="r", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["channel_oid"] == channel_oid:
                return row["language"]


def worklow(msc: MediaServerClient, channel_oid: str, update: bool = False):
    info = get_channel_videos(msc, channel_oid)

    for video in info["video_oids"]:
        oid_already_exists = oid_exists(video["oid"])
        if update or not oid_already_exists:
            channel_language = get_channel_language(video["parent_oid"])
            channel_language = (
                channel_language
                if channel_language != "" and channel_language != "fr/en"
                else None
            )
            enrichment_id = request_enrichment(video["oid"], language=channel_language)

            if update and oid_already_exists:
                delete_line(video["oid"])

            add_line(video["oid"], enrichment_id, channel_language)

    print_table()


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", type=str, help="Specify the channel OID")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Indicate if you want to update already treated videos",
    )
    parser.add_argument(
        "--csv", type=str, help="Specify a CSV file if multiple channels to treat"
    )

    args = parser.parse_args()

    channel_oid = args.channel
    update = args.update
    csv_file = args.csv

    print(f"Channel: {channel_oid}")
    print(f"Update: {update}")
    print(f"CSV File: {csv_file}")

    msc = MediaServerClient(CONFIG_FILE)
    msc.check_server()

    conn = sqlite3.connect(DATABASE_URL)
    initiate_database()

    if channel_oid:
        worklow(msc, channel_oid, update)
    elif csv_file:
        with open(csv_file, mode="r", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                worklow(msc, row["channel_oid"], update)

    conn.close()
