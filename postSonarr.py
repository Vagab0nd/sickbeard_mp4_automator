#!/usr/bin/env python3
import os
import sys
import requests
import time
import shutil
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.metadata import MediaType
from resources.mediaprocessor import MediaProcessor


def rescanAndWait(host, port, webroot, apikey, protocol, seriesid, log, retries=6, delay=10):
    headers = {'X-Api-Key': apikey}
    # First trigger rescan
    payload = {'name': 'RescanSeries', 'seriesId': seriesid}
    url = protocol + host + ":" + str(port) + webroot + "/api/command"
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.info("Sonarr response RescanSeries command: ID %d %s." % (rstate['id'], rstate['state']))
    log.debug(str(rstate))

    # Then wait for it to finish
    url = protocol + host + ":" + str(port) + webroot + "/api/command/" + str(rstate['id'])
    log.info("Requesting episode information from Sonarr for series ID %s." % seriesid)
    r = requests.get(url, headers=headers)
    command = r.json()
    attempts = 0
    while command['state'].lower() not in ['complete', 'completed'] and attempts < retries:
        log.info("State: %s." % (command['state']))
        time.sleep(delay)
        r = requests.get(url, headers=headers)
        command = r.json()
        attempts += 1
    log.info("Final state: %s." % (command['state']))
    log.debug(str(command))
    return command['state'].lower() in ['complete', 'completed']


def getEpisodeInformation(host, port, webroot, apikey, protocol, episodeid, log):
    headers = {'X-Api-Key': apikey}
    url = protocol + host + ":" + str(port) + webroot + "/api/episode?seriesId=" + seriesid
    log.info("Requesting updated episode information from Sonarr for series ID %s." % seriesid)
    r = requests.get(url, headers=headers)
    payload = r.json()
    sonarrepinfo = None
    for ep in payload:
        if int(ep['episodeNumber']) == episode and int(ep['seasonNumber']) == season:
            return ep
    return None


def renameFile(inputfile, log):
    filename, fileext = os.path.splitext(inputfile)
    outputfile = "%s.rnm%s" % (filename, fileext)
    i = 2
    while os.path.isfile(outputfile):
        outputfile = "%s.rnm%d%s" % (filename, i, fileext)
        i += 1
    os.rename(inputfile, outputfile)
    log.debug("Renaming file %s to %s." % (inputfile, outputfile))
    return outputfile


def renameSeries(host, port, webroot, apikey, protocol, seriesid, log):
    headers = {'X-Api-Key': apikey}
    # First trigger rescan
    payload = {'name': 'RenameSeries', 'seriesIds': [seriesid]}
    url = protocol + host + ":" + str(port) + webroot + "/api/command"
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.info("Sonarr response RenameSeries command: ID %d %s." % (rstate['id'], rstate['state']))
    log.debug(str(rstate))


def backupSubs(inputpath, mp, log, extension=".backup"):
    dirname, filename = os.path.split(inputpath)
    files = []
    output = {}
    for r, _, f in os.walk(dirname):
        for file in f:
            files.append(os.path.join(r, file))
    for filepath in files:
        if filepath.startswith(os.path.splitext(filename)[0]):
            info = mp.isValidSubtitleSource(filepath)
            if info:
                newpath = filepath + extension
                shutil.copy2(filepath, newpath)
                output[newpath] = filepath
                log.info("Copying %s to %s." % (filepath, newpath))
    return output


def restoreSubs(subs, log):
    for k in subs:
        try:
            os.rename(k, subs[k])
            log.info("Restoring %s to %s." % (k, subs[k]))
        except:
            os.remove(k)
            log.exception("Unable to restore %s, deleting." % (k))


log = getLogger("SonarrPostProcess")

log.info("Sonarr extra script post processing started.")

if os.environ.get('sonarr_eventtype') == "Test":
    sys.exit(0)

settings = ReadSettings()

log.debug(os.environ)

inputfile = os.environ.get('sonarr_episodefile_path')
original = os.environ.get('sonarr_episodefile_scenename')
tvdb_id = int(os.environ.get('sonarr_series_tvdbid'))
imdb_id = os.environ.get('sonarr_series_imdbid')
season = int(os.environ.get('sonarr_episodefile_seasonnumber'))
seriesid = os.environ.get('sonarr_series_id')

try:
    episode = int(os.environ.get('sonarr_episodefile_episodenumbers'))
except:
    episode = int(os.environ.get('sonarr_episodefile_episodenumbers').split(",")[0])

mp = MediaProcessor(settings)

log.debug("Input file: %s." % inputfile)
log.debug("Original name: %s." % original)
log.debug("TVDB ID: %s." % tvdb_id)
log.debug("Season: %s episode: %s." % (season, episode))
log.debug("Sonarr series ID: %s." % seriesid)

try:
    if settings.Sonarr.get('rename'):
        # Prevent asynchronous errors from file name changing
        mp.settings.waitpostprocess = True
        try:
            inputfile = renameFile(inputfile, log)
        except:
            log.exception("Error renaming inputfile")

    success = mp.fullprocess(inputfile, MediaType.TV, tvdbid=tvdb_id, imdbid=imdb_id, season=season, episode=episode, original=original)

    if success:
        # Update Sonarr to continue monitored status
        try:
            host = settings.Sonarr['host']
            port = settings.Sonarr['port']
            webroot = settings.Sonarr['webroot']
            apikey = settings.Sonarr['apikey']
            ssl = settings.Sonarr['ssl']
            protocol = "https://" if ssl else "http://"

            log.debug("Sonarr host: %s." % host)
            log.debug("Sonarr port: %s." % port)
            log.debug("Sonarr webroot: %s." % webroot)
            log.debug("Sonarr apikey: %s." % apikey)
            log.debug("Sonarr protocol: %s." % protocol)

            if apikey != '':
                headers = {'X-Api-Key': apikey}

                subs = backupSubs(success[0], mp, log)

                if rescanAndWait(host, port, webroot, apikey, protocol, seriesid, log):
                    log.info("Rescan command completed")

                    sonarrepinfo = getEpisodeInformation(host, port, webroot, apikey, protocol, seriesid, log)
                    if not sonarrepinfo:
                        log.error("No valid episode information found, aborting.")
                        sys.exit(1)

                    if not sonarrepinfo.get('hasFile'):
                        log.warning("Rescanned episode does not have a file, attempting second rescan.")
                        if rescanAndWait(host, port, webroot, apikey, protocol, seriesid, log):
                            sonarrepinfo = getEpisodeInformation(host, port, webroot, apikey, protocol, seriesid, log)
                            if not sonarrepinfo:
                                log.error("No valid episode information found, aborting.")
                                sys.exit(1)
                            if not sonarrepinfo.get('hasFile'):
                                log.warning("Rescanned episode still does not have a file, will not set to monitored to prevent endless loop.")
                                sys.exit(1)
                            else:
                                log.info("File found after second rescan.")
                        else:
                            log.error("Rescan command timed out")
                            restoreSubs(subs, log)
                            sys.exit(1)

                    if len(subs) > 0:
                        log.debug("Restoring %d subs and triggering a final rescan." % (len(subs)))
                        restoreSubs(subs, log)
                        rescanAndWait(host, port, webroot, apikey, protocol, seriesid, log)

                    # Then set that episode to monitored
                    sonarrepinfo['monitored'] = True
                    log.debug("Sending PUT request with following payload:")
                    log.debug(str(sonarrepinfo))

                    url = protocol + host + ":" + str(port) + webroot + "/api/episode/" + str(sonarrepinfo['id'])
                    r = requests.put(url, json=sonarrepinfo, headers=headers)
                    success = r.json()

                    log.debug("PUT request returned:")
                    log.debug(str(success))
                    log.info("Sonarr monitoring information updated for episode %s." % success['title'])

                    renameSeries(host, port, webroot, apikey, protocol, seriesid, log)
                else:
                    log.error("Rescan command timed out")
                    sys.exit(1)
            else:
                log.error("Your Sonarr API Key is blank. Update autoProcess.ini to enable status updates.")
        except:
            log.exception("Sonarr monitor status update failed.")
    else:
        log.info("Processing returned False.")
        sys.exit(1)
except:
    log.exception("Error processing file")
    sys.exit(1)
