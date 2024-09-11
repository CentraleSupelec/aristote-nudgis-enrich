import sqlite3
from flask import Flask, jsonify, request, redirect, Response
from ms_client.client import MediaServerClient, MediaServerRequestError

import os
from dotenv import load_dotenv
from aristote import get_enrichment_version, get_transcript

load_dotenv(".env")

DATABASE_URL = os.environ["DATABASE_URL"]
CONFIG_FILE = os.environ["CONFIG_FILE"]

app = Flask(__name__)


def get_oid_by_enrichment_id(conn: sqlite3.Connection, enrichment_id: str) -> str | None:
    cursor = conn.cursor()
    cursor.execute('SELECT oid FROM enrichment_requests WHERE enrichment_id = ?', (enrichment_id,))
    row = cursor.fetchone()

    if row:
        return row[0]
    return None

def update_status_by_oid(conn: sqlite3.Connection, oid: str, status: str):
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE enrichment_requests
        SET status = ?
        WHERE oid = ?
    ''', (status, oid))
    conn.commit()


def get_media_best_resource_url(msc: MediaServerClient, oid) -> str:
    resources = msc.api('medias/resources-list/', params=dict(oid=oid))['resources']
    resources.sort(key=lambda a: -a['file_size'])
    if not resources:
        print('Media has no resources.')
        return
    best_quality = None
    for r in resources:
        if r['format'] != 'm3u8':
            best_quality = r
            break
    if not best_quality:
        print('Warning: No resource file can be downloaded for video %s!' % (oid,))
        print('Resources: %s' % resources)
        raise Exception('Could not download any resource from list: %s.' % resources)

    print('Best quality file for video %s: %s' % (oid, best_quality['file']))

    if not best_quality['format'] in ('youtube', 'embed'):
        # download resource
        url_resource = msc.api(
            'download/',
            method='get',
            params=dict(oid=oid, url=best_quality['file'], redirect='no')
        )['url']
        return url_resource


@app.route('/api', methods=['GET'])
def get_data():
    return jsonify({"message": "Hello, World!"})


@app.route('/webhook', methods=['POST'])
def webhook():
    msc = MediaServerClient(CONFIG_FILE)
    msc.check_server()
    data = request.get_json()

    enrichment_id = data['id']
    conn = sqlite3.connect(DATABASE_URL)
    oid = get_oid_by_enrichment_id(conn=conn, enrichment_id=enrichment_id)

    if data['status'] == 'SUCCESS':
        initial_version_id = data['initialVersionId']
        print(f"OID : {oid}")
        if oid:
            update_status_by_oid(conn=conn, oid=oid, status='SUCCESS')
            enrichment_version = get_enrichment_version(enrichment_id, initial_version_id)
            language = enrichment_version['transcript']['language']
            transcript = get_transcript(enrichment_id, initial_version_id, language)
            msc = MediaServerClient(CONFIG_FILE)
            msc.check_server()
            print(f"// Sending subtitles in {language}")
            subtitles_add_response = msc.api(
                '/subtitles/add',
                method='post',
                data={'oid': oid, 'lang': language, 'validated': 'yes'},
                files = {'file': (f"{oid}_{language}.srt", transcript, 'text/plain')}
            )
            print(subtitles_add_response['message'])

            translate_to = enrichment_version['translateTo']
            if translate_to:
                translated_transcript = get_transcript(enrichment_id, initial_version_id, translate_to)
                print(f"// Sending translated subtitles in {translate_to}")
                translated_subtitles_add_response = msc.api(
                    '/subtitles/add',
                    method='post',
                    data={'oid': oid, 'lang': translate_to, 'validated': 'yes'},
                    files = {'file': (f"{oid}_{translate_to}.srt", translated_transcript, 'text/plain')}
                )
                print(translated_subtitles_add_response['message'])

        return ""
    elif data['status'] == 'FAILURE':
        update_status_by_oid(conn=conn, oid=oid, status='FAILURE')
        return ""


@app.route('/export/<string:oid>', methods=['GET'])
def export_data(oid):
    msc = MediaServerClient(CONFIG_FILE)
    msc.check_server()
    try:
        url_resource = get_media_best_resource_url(msc, oid)
    except MediaServerRequestError as error:
        return Response("OID not found", status=error.status_code)
    print(url_resource)
    return redirect(url_resource)


if __name__ == '__main__':
    app.run(host='localhost',port=8085, debug=True)
