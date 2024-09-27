from datetime import datetime
import sqlite3
import requests
from flask import Flask, request, Response, stream_with_context
from ms_client.client import MediaServerClient, MediaServerRequestError
from urllib.parse import urlparse
import logging
import os
from dotenv import load_dotenv
from aristote import get_enrichment_version, get_transcript, request_new_enrichment

logger = logging.getLogger(__name__)
load_dotenv(".env")

DATABASE_URL = os.environ["DATABASE_URL"]
CONFIG_FILE = os.environ["CONFIG_FILE"]
ARISTOTE_MARKER = "aristote_generated"

app = Flask(__name__)


def get_oid_by_enrichment_id(
    conn: sqlite3.Connection, enrichment_id: str
) -> str | None:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT oid FROM enrichment_requests WHERE enrichment_id = ?", (enrichment_id,)
    )
    row = cursor.fetchone()

    if row:
        return row[0]
    return None


def update_status_by_oid(conn: sqlite3.Connection, oid: str, status: str):
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


def update_enrichment_notification_received_at(
    conn: sqlite3.Connection, enrichment_id: str
):
    cursor = conn.cursor()

    enrichment_notification_received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        UPDATE enrichment_requests
        SET enrichment_notification_received_at = ?
        WHERE enrichment_id = ?
    """,
        (enrichment_notification_received_at, enrichment_id),
    )
    conn.commit()


def update_language_by_oid(conn: sqlite3.Connection, oid: str, language: str):
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE enrichment_requests
        SET language = ?
        WHERE oid = ?
    """,
        (language, oid),
    )
    conn.commit()


def get_media_best_resource_url(msc: MediaServerClient, oid) -> str:
    resources = msc.api("medias/resources-list/", params=dict(oid=oid))["resources"]
    resources.sort(key=lambda a: a["file_size"])
    if not resources:
        logger.debug("Media has no resources.")
        return
    best_quality = None
    for r in resources:
        if r["format"] != "m3u8":
            best_quality = r
            break
    if not best_quality:
        logger.warning("No resource file can be downloaded for video %s!" % (oid,))
        logger.warning("Resources: %s" % resources)
        raise Exception("Could not download any resource from list: %s." % resources)

    logger.debug("Smallest file for video %s: %s" % (oid, best_quality["file"]))

    if best_quality["format"] not in ("youtube", "embed"):
        url_resource = msc.api(
            "download/",
            method="get",
            params=dict(oid=oid, url=best_quality["file"], redirect="no"),
        )["url"]
        return url_resource
    else:
        return None


def handle_enrichment(
    conn: sqlite3.Connection,
    msc: MediaServerClient,
    oid: str,
    enrichment_id: str,
    enrichment_version_id: str,
    status,
):
    if status == "SUCCESS":
        if oid:
            enrichment_version = get_enrichment_version(
                enrichment_id, enrichment_version_id
            )
            language = enrichment_version["transcript"]["language"]
            translate_to = enrichment_version["translateTo"]

            if translate_to:
                logger.debug(f"Enrichment translated to {translate_to}")
                update_status_by_oid(conn=conn, oid=oid, status="SUCCESS")
            else:
                logger.debug("Requesting enrichment translation")
                if language is not None and language != "":
                    update_status_by_oid(conn=conn, oid=oid, status="TRANSCRIBED")
                    update_language_by_oid(conn=conn, oid=oid, language=language)
                    request_new_enrichment(enrichment_id, language)
                else:
                    update_status_by_oid(
                        conn=conn, oid=oid, status="TRANSCRIBED_NO_LANGUAGE"
                    )
                return
            transcript = get_transcript(enrichment_id, enrichment_version_id, language)
            subtitles_get_response = msc.api(
                "/subtitles", method="get", params={"oid": oid}
            )

            subs = subtitles_get_response["subtitles"]

            for sub in subs:
                if str(sub["title"]).startswith(ARISTOTE_MARKER):
                    logger.debug("Deleting found Aristote subtitle")
                    sub_id = sub["id"]
                    subtitles_delete_response = msc.api(
                        "/subtitles/delete",
                        method="post",
                        data={"id": sub_id},
                    )
                    logger.debug(subtitles_delete_response["message"])

            logger.debug(f"Submitting subtitles in {language}")
            subtitles_add_response = msc.api(
                "/subtitles/add",
                method="post",
                data={
                    "oid": oid,
                    "lang": language,
                    "validated": "yes",
                    "title": f"{ARISTOTE_MARKER}_{language}",
                },
                files={
                    "file": (
                        f"{ARISTOTE_MARKER}_{oid}_{language}.srt",
                        transcript,
                        "text/plain",
                    )
                },
            )
            logger.debug(subtitles_add_response["message"])

            if translate_to:
                translated_transcript = get_transcript(
                    enrichment_id, enrichment_version_id, translate_to
                )
                logger.debug(f"Submitting translated subtitles in {translate_to}")
                translated_subtitles_add_response = msc.api(
                    "/subtitles/add",
                    method="post",
                    data={
                        "oid": oid,
                        "lang": translate_to,
                        "validated": "yes",
                        "title": f"{ARISTOTE_MARKER}_{translate_to}",
                    },
                    files={
                        "file": (
                            f"{ARISTOTE_MARKER}_{oid}_{translate_to}.srt",
                            translated_transcript,
                            "text/plain",
                        )
                    },
                )
                logger.debug(translated_subtitles_add_response["message"])
        return
    elif status == "FAILURE":
        update_status_by_oid(conn=conn, oid=oid, status="FAILURE")
        return


@app.route("/webhook", methods=["POST"])
def webhook():
    msc = MediaServerClient(CONFIG_FILE)
    msc.conf["TIMEOUT"] = 30
    msc.check_server()
    data = request.get_json()
    enrichment_id = data["id"]
    status = data["status"]
    enrichment_version_id = data["initialVersionId"]
    conn = sqlite3.connect(DATABASE_URL)
    update_enrichment_notification_received_at(conn=conn, enrichment_id=enrichment_id)
    oid = get_oid_by_enrichment_id(conn=conn, enrichment_id=enrichment_id)
    logger.info(f"OID : {oid}")
    handle_enrichment(conn, msc, oid, enrichment_id, enrichment_version_id, status)
    return ""


@app.route("/export/<string:oid>", methods=["GET"])
def export_data(oid):
    msc = MediaServerClient(CONFIG_FILE)
    msc.conf["TIMEOUT"] = 30
    try:
        msc.check_server()
    except Exception:
        return Response("Ubicast server timeout", status=504)

    try:
        url_resource = get_media_best_resource_url(msc, oid)
    except MediaServerRequestError as error:
        return Response("OID not found", status=error.status_code)

    if url_resource is None:
        conn = sqlite3.connect(DATABASE_URL)
        update_status_by_oid(conn, oid, "NOT_DOWNLOADABLE")
        return Response("No downloadable resource found", status=500)

    media_response = requests.get(url_resource, stream=True)

    if media_response.status_code != 200:
        return Response("Failed to download video", status=500)

    parsed_url = urlparse(url_resource)
    filename = os.path.basename(parsed_url.path)
    mime_type = media_response.headers.get("Content-Type")

    def generate():
        for chunk in media_response.iter_content(chunk_size=1024 * 1024):
            yield chunk

    return Response(
        stream_with_context(generate()),
        content_type=mime_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    logger.setLevel(logging.DEBUG)
    app.run(host="localhost", port=8085, debug=True)
