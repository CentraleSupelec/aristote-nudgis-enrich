#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Script to get the size used by all resources of media in a given channel or in whole catalog if no channel is specified.
The API key used to run this script must have the permission to access resources tab and all media.

To use this script clone MediaServer client and put this file in a sub dir in the client dir.
git clone https://github.com/UbiCastTeam/mediaserver-client
mkdir examples
mv "this file" mediaserver-client/examples
'''
import csv
from datetime import datetime
import os
import sqlite3
import sys
from dotenv import load_dotenv
from ms_client.client import MediaServerClient

from aristote import request_enrichment

load_dotenv(".env")

DATABASE_URL = os.environ["DATABASE_URL"]
CONFIG_FILE = os.environ["CONFIG_FILE"]

def get_channel_videos(msc, oid, info=None):
    if info is None:
        info = dict(channels=0, video_oids=[])
    print('//// Channel %s' % oid)
    print('Making request on channels/content/ (parent_oid=%s)' % oid)
    response = msc.api('channels/content/', params=dict(parent_oid=oid, content='cvlp'))
    if response.get('channels'):
        for item in response['channels']:
            info['channels'] += 1
            get_channel_videos(msc, item['oid'], info)
    if response.get('videos'):
        for item in response['videos']:
            print('// Media %s' % item['oid'])
            info['video_oids'].append(dict(oid=item['oid'], parent_oid=oid, type=item['type']))
    return info

def print_table():
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM enrichment_requests')
    rows = cursor.fetchall()

    column_names = [description[0] for description in cursor.description]
    print(column_names)

    for row in rows:
        print(row)

def initiate_database():
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS enrichment_requests (
        oid TEXT PRIMARY KEY,
        enrichment_id TEXT,
        request_sent_at DATETIME,
        enrichment_notification_received_at DATETIME,
        language TEXT,
        status TEXT
    )
    ''')

    conn.commit()

def add_line(oid: str, enrichment_id: str, language: str):
    cursor = conn.cursor()

    request_sent_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    status = 'PENDING'

    cursor.execute('''
        INSERT INTO enrichment_requests (oid, enrichment_id, request_sent_at, language, status)
        VALUES (?, ?, ?, ?, ?)
    ''', (oid, enrichment_id, request_sent_at, language, status))

    conn.commit()

def oid_exists(oid):
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM enrichment_requests WHERE oid = ?', (oid,))

    row = cursor.fetchone()

    if row:
        return True
    return False

def get_channel_language(channel_oid: str) -> str:
    with open("channels.csv", mode='r', newline='') as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["channel_oid"] == channel_oid:
                return row['language']

def worklow(msc: MediaServerClient, channel_oid: str):
    info = get_channel_videos(msc, channel_oid)

    for video in info['video_oids']:
        if not oid_exists(video['oid']):
            print('Adding new line')
            channel_language = get_channel_language(video["parent_oid"])
            channel_language = channel_language if channel_language != '' and channel_language != 'fr/en' else None
            enrichment_id = request_enrichment(video['oid'], langauge=channel_language)
            add_line(video['oid'], enrichment_id, channel_language)

    print_table()

if __name__ == '__main__':
    msc = MediaServerClient(CONFIG_FILE)
    msc.check_server()
    channel_oid = sys.argv[1] if len(sys.argv) > 1 else ''
    conn = sqlite3.connect(DATABASE_URL)
    initiate_database()
    worklow(msc, channel_oid)
    conn.close()
