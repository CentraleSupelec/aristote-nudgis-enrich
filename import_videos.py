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
from datetime import datetime, timedelta
import os
import sqlite3
from dotenv import load_dotenv
from ms_client.client import MediaServerClient
import argparse
import logging

from aristote import (
    request_enrichment,
    get_enrichment,
    get_latest_enrichment_version,
    request_new_enrichment,
)
from ubicast import handle_enrichment, logger

load_dotenv(".env")

DATABASE_URL = os.environ["DATABASE_URL"]
CONFIG_FILE = os.environ["CONFIG_FILE"]


def get_channel_videos(msc, oid, info=None):
    if info is None:
        info = dict(channels=0, video_oids=[])
    logger.debug("Making request on channels/content/ (parent_oid=%s)" % oid)
    response = msc.api("channels/content/", params=dict(parent_oid=oid, content="cvlp"))
    if response.get("channels"):
        for item in response["channels"]:
            info["channels"] += 1
            get_channel_videos(msc, item["oid"], info)
    if response.get("videos"):
        for item in response["videos"]:
            logger.debug("Media %s" % item["oid"])
            info["video_oids"].append(
                dict(
                    oid=item["oid"],
                    parent_oid=oid,
                    type=item["type"],
                    slug=item["slug"],
                )
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
        status TEXT,
        name TEXT,
        parent_oid TEXT
    )
    """
    )

    conn.commit()


def update_status_by_oid(oid: str, status: str):
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE enrichment_requests
        SET status = ?
        WHERE oid = ?
    """,
        (status, oid),
    )
    conn.commit()


def get_status_by_oid(oid: str) -> str | None:
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM enrichment_requests WHERE oid = ?", (oid,))
    row = cursor.fetchone()

    if row:
        return row[0]
    return None


def get_enrichment_id_by_oid(oid: str) -> str | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT enrichment_id FROM enrichment_requests WHERE oid = ?", (oid,)
    )
    row = cursor.fetchone()

    if row:
        return row[0]
    return None


def add_line(oid: str, enrichment_id: str, language: str, name: str, parent_oid: str):
    cursor = conn.cursor()

    request_sent_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "PENDING"

    cursor.execute(
        """
        INSERT INTO enrichment_requests (oid, enrichment_id, request_sent_at, language, status, name, parent_oid)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (oid, enrichment_id, request_sent_at, language, status, name, parent_oid),
    )

    conn.commit()
    logger.debug(f"Enrichment request with oid: {oid} has been added.")


def delete_line(oid: str):
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM enrichment_requests WHERE oid = ?
        """,
        (oid,),
    )

    conn.commit()

    logger.debug(f"Enrichment request with oid: {oid} has been deleted.")


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


def worklow(
    msc: MediaServerClient, channel_oid: str, update: str = None, limit: int = None
):
    global videos_count
    global enrichment_requests_count
    global stuck_videos
    global enriched_videos

    info = get_channel_videos(msc, channel_oid)
    videos_count += len(info["video_oids"])

    for video in info["video_oids"]:
        if limit and enrichment_requests_count >= limit:
            break

        oid = video["oid"]
        parent_oid = video["parent_oid"]
        name = video["slug"]

        oid_already_exists = oid_exists(oid)

        if update == "quiz" and oid_already_exists:
            known_status = get_status_by_oid(oid)
            if known_status == "SUCCESS":
                enrichment_id = get_enrichment_id_by_oid(oid)
                latest_enrichment_version = get_latest_enrichment_version(enrichment_id)
                if latest_enrichment_version["enrichmentVersionMetadata"] is None:
                    update_status_by_oid(oid, "PENDING")
                    request_new_enrichment(
                        enrichment_id, latest_enrichment_version["language"]
                    )
                    enrichment_requests_count += 1
                    logger.debug(
                        f"OID : {oid} | Enrichment : {enrichment_id} Requested quiz generation"
                    )
                    enriched_videos.append({"oid": oid, "enrichmentId": enrichment_id})

        stuck = False
        if update == "stuck" and oid_already_exists:
            known_status = get_status_by_oid(oid)
            if known_status in ["PENDING", "FAILURE", "TRANSCRIBED"]:
                enrichment_id = get_enrichment_id_by_oid(oid)
                enrichment = get_enrichment(enrichment_id)
                status = enrichment["status"]
                upload_started_at = enrichment["uploadStartedAt"]
                ancient_upload = True
                if upload_started_at:
                    upload_started_at = datetime.fromisoformat(
                        enrichment["uploadStartedAt"]
                    )
                    now = datetime.now(upload_started_at.tzinfo)
                    now_minus_delta = now - timedelta(hours=2)
                    ancient_upload = upload_started_at < now_minus_delta

                if status == "FAILURE" or (
                    status == "UPLOADING_MEDIA" and ancient_upload
                ):
                    logger.debug(f"OID : {oid} | Enrichment : {enrichment_id} is stuck")
                    stuck = True
                    stuck_videos.append(
                        {"oid": oid, "enrichmentId": enrichment_id, "status": status}
                    )
                elif status == "SUCCESS":
                    logger.debug(
                        f"OID : {oid} | Enrichment : {enrichment_id} has been treated but missed webhook"
                    )
                    latest_enrichment_version = get_latest_enrichment_version(
                        enrichment_id
                    )["id"]
                    handle_enrichment(
                        conn, msc, oid, enrichment_id, latest_enrichment_version, status
                    )
                    stuck_videos.append(
                        {"oid": oid, "enrichmentId": enrichment_id, "status": status}
                    )

        force_update = update == "all" or (update == "stuck" and stuck)

        if not oid_already_exists or force_update:
            channel_language = get_channel_language(video["parent_oid"])
            channel_language = (
                channel_language
                if channel_language != "" and channel_language != "fr/en"
                else None
            )

            ignore_video = False

            if oid_already_exists:
                known_status = get_status_by_oid(oid)
                ignore_video = known_status in [
                    "TRANSCRIBED_NO_LANGUAGE",
                    "NOT_DOWNLOADABLE",
                ]

            if not oid_already_exists or (
                oid_already_exists and force_update and not ignore_video
            ):
                enrichment_id = request_enrichment(oid, language=channel_language)
                enrichment_requests_count += 1

            if oid_already_exists and force_update and not ignore_video:
                delete_line(oid)

            if not ignore_video:
                add_line(oid, enrichment_id, channel_language, name, parent_oid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", type=str, help="Specify the channel OID")
    parser.add_argument(
        "--update",
        type=str,
        help="Specify if to force update all videos or only those stuck 'all' or 'stuck' or 'quiz'",
    )
    parser.add_argument(
        "--csv", type=str, help="Specify a CSV file if multiple channels to treat"
    )
    parser.add_argument(
        "--limit", type=str, help="Specify a limit for enrichment requests"
    )
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    channel_oid = args.channel
    update = args.update
    csv_file = args.csv
    limit = int(args.limit) if args.limit else None
    if args.debug:
        logger.setLevel(logging.DEBUG)

    logger.info(f"Channel: {channel_oid}")
    logger.info(f"Update: {update}")
    logger.info(f"CSV File: {csv_file}")
    logger.info(f"Limit : {limit}")

    msc = MediaServerClient(CONFIG_FILE)
    msc.check_server()

    conn = sqlite3.connect(DATABASE_URL)
    initiate_database()

    videos_count = 0
    enrichment_requests_count = 0
    stuck_videos = []
    enriched_videos = []

    if channel_oid:
        worklow(msc, channel_oid, update, limit)
    elif csv_file:
        with open(csv_file, mode="r", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                worklow(msc, row["channel_oid"], update, limit)

    logger.info(f"Total number of videos : {videos_count}")

    if update == "stuck":
        logger.info(f"Number of stuck videos : {len(stuck_videos)}")
        logger.info(stuck_videos)

    if update == "quiz":
        logger.info(f"Requested enrichment for {len(enriched_videos)} videos")
        logger.info(enriched_videos)

    conn.close()
