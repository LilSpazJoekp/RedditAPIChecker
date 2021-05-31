"""Checks the public API page at dev/api for changes"""
import argparse
import io
import time
from copy import copy
from datetime import datetime
from difflib import unified_diff
from json import dump, load, loads, dumps
from os import listdir, mkdir
from os.path import isdir, isfile, join

import discord
import tabulate
from BotUtils import BotServices
from bs4 import BeautifulSoup, NavigableString, Tag
from praw.endpoints import API_PATH
from github import Github, InputFileContent

from config import github_token, webhook

import requests
from discord import Webhook, RequestsWebhookAdapter, File

webhook = Webhook.partial(*webhook, adapter=RequestsWebhookAdapter())


g = Github(github_token)
gist = g.get_gist('28f68ce32bca9a8f6aa1da1c52e1e72a')

services = BotServices('RedditAPIChecker')
reddit = services.reddit('Lil_SpazBot')
log = services.logger()
thread = reddit.inbox.message('v47hov')

def parseEndpoints():
    url = 'https://www.reddit.com/dev/api'
    html_content = requests.get(url, headers={'user-agent': 'Reddit API diff checker by u/Lil_SpazJoekp'}).text
    soup = BeautifulSoup(html_content, 'lxml')
    endpoints = soup.findAll(attrs={'class': 'endpoint'})
    parsed = {}
    for i in endpoints:
        details = {}
        contents = i.contents[1]
        params = None
        urlVariants = []
        paramsIndex = 2
        if i.contents[2].attrs['class'][0] == 'uri-variants':
            variants = i.contents[2]
            urlVariants = []
            for variant in variants.contents:
                parts = []
                for part in variant.contents:
                    if isinstance(part, NavigableString):
                        parts.append(part.strip('→ []'))
                    elif isinstance(part, Tag):
                        if part.attrs['class'][0] == 'placeholder':
                            parts.append(f'{{{part.text}}}')
                urlVariants.append((variant.attrs['id'], ''.join(parts)))
            paramsIndex += 1
        if len(i.contents[paramsIndex].contents) > 2:
            params = {p.contents[0].text: p.contents[1].text.strip() for p in i.contents[paramsIndex].contents[2].contents}
            details['params'] = params
        try:
            details['description'] = i.contents[paramsIndex].contents[0].contents[0].text
        except IndexError:
            details['description'] = None
        elements = [i.name for i in contents]
        firstSpan = elements.index('span')
        if 'span' in elements[firstSpan + 1:]:
            secondSpan = elements[firstSpan + 1:].index('span')
        else:
            secondSpan = len(elements[firstSpan + 1:])
        parts = []
        for element in [i for i in contents][firstSpan + 1: secondSpan + 1]:
            if isinstance(element, NavigableString):
                parts.append(element.strip('[]'))
            elif isinstance(element, Tag):
                if element.attrs['class'][0] == 'placeholder':
                    parts.append(f'{{{element.text}}}')
                else:
                    log.info(element.attrs['class'])
            else:
                log.error('last else:', element)
        if urlVariants:
            for variantID, variantUrl in urlVariants:
                details['url'] = variantUrl
                parsed[variantID] = copy(details)
        else:
            details['url'] = ''.join(parts)
            endpointId = i.attrs['id']
            if ':' in details['url']:
                details['url'] = '/'.join([f"{{{item.strip(':')}}}" if item.startswith(':') else item for item in details['url'].split('/')])
            parsed[endpointId] = details
    return parsed

def printEndpointsNotInPRAW(parsed):
    replacements = [
        ('api/widget', 'r/{subreddit}/api/widget'),
        ('srname', 'subreddit'),
        ('{filterpath}', 'user/{user}'),
        ('{username}', '{user}'),
        ('{conversation_id}', '{id}'),
        (':conversation_id', '{id}'),
        ('live/{thread}', 'live/{id}')
    ]
    prawEndpoints = [i.strip('/') for i in API_PATH.values()]
    missing = []
    for name, endpoint in parsed.items():
        endpointUrl = endpoint['url'].strip('/')
        for old, new in replacements:
            endpointUrl = endpointUrl.replace(old, new)
            name = name.replace(old, new)
        if endpointUrl not in prawEndpoints:
            missing.append([name, endpointUrl])
    print(tabulate.tabulate(missing, ['Reddit API', 'PRAW'], tablefmt='fancy_grid'))

def main():
    '''Runs the main function.

    usage: pre_push.py [-h] [-n] [-u] [-a]

    Check Reddit's API public endpoints for changes

    '''
    parser = argparse.ArgumentParser(description='Check dev/api endpoints for changes')
    parser.add_argument('-o', '--output', action='store', help='File to write endpoints to.', default='endpoints.json')
    parser.add_argument('-c', '--check', action='store_true', help='Check if there are any changes,', default=True)
    parser.add_argument('-e', '--existing', action='store', help='File to existing endpoints')
    parser.add_argument('-p', '--print', action='store_true', help='Print endpoints not in PRAW', default=False)
    parser.add_argument('-d', '--changes-dir', action='store', help='Dir to store changes', default='changes')
    args = parser.parse_args()
    existingEndpoints = args.existing
    outputFilename = args.output
    check = args.check
    printDiff = args.print
    changesDir = args.changes_dir
    changes = False
    file = None
    try:
        if existingEndpoints and isfile(existingEndpoints):
            with open(existingEndpoints) as f:
                old_api = f.read()
                existing = loads(old_api)
        else:
            old_api = gist.files['API.json'].content
            existing = loads(old_api)
        parsed = parseEndpoints()
        current_api = dumps(parsed, indent=4)
        if check:
            if existing != parsed:
                changes = True
                if not isdir(changesDir):
                    mkdir(changesDir)
                currentChangesDir = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                revisions = listdir(changesDir)
                revisions.sort()
                mkdir(join(changesDir, currentChangesDir))
                if len(revisions) > 1:
                    previous_revision = revisions[-1]
                with open(join(changesDir, currentChangesDir, 'oldEndpoints.json'), 'w') as f:
                    dump(existing, f, indent=4)
                with open(join(changesDir, currentChangesDir, 'oldEndpoints.json'), 'r') as f:
                    old = f.readlines()

                with open(join(changesDir, currentChangesDir, 'newEndpoints.json'), 'w') as f:
                    f.write(current_api)
                with open(join(changesDir, currentChangesDir, 'newEndpoints.json'), 'r') as f:
                    new = f.readlines()

                log.info('Changes were detected!')
                diff_string = ''.join(list(unified_diff(old, new, fromfile=f'API_{previous_revision}.json', tofile=f'API_{currentChangesDir}.json')))
                diff_file = io.BytesIO(diff_string.encode())
                diff_file.seek(0)
                gist.edit(files={'API.json': InputFileContent(current_api), 'changes.diff': InputFileContent(diff_string)})
                file = File(diff_file, f"API_{currentChangesDir}.diff")
        if outputFilename:
            with open(outputFilename, 'w') as f:
                f.write(current_api)
        if printDiff:
            printEndpointsNotInPRAW(parsed)
    except Exception as error:
        log.exception(error)
    return changes, file

if __name__ == '__main__':
    log.info('Checking Reddit API...')
    changes, file = main()
    if changes:
        log.info('Change detected')
        embed = discord.Embed(title='Changes Detected', description=datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))
        embed.add_field(name='Gist', value='[API.json](https://gist.github.com/LilSpazJoekp/28f68ce32bca9a8f6aa1da1c52e1e72a/revisions?diff=split)')
        webhook.send("<@393801572858986496>", embed=embed, username='Reddit API Changes', file=file)
    else:
        log.info('No change detected')
    time.sleep(43230)
