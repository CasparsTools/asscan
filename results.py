#!/usr/bin/env python3

from os import listdir, stat
from datetime import datetime, timezone
from os.path import join, isfile, isdir
import json, sys
from scanners import ScanJob
from collections import defaultdict
import notes
import ipaddress
import os
import re
from log import log

re_uuid = re.compile('^[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}\Z', re.I)

def sorted_addresses(addrs):
    return sorted(addrs, key=lambda x:ipaddress.ip_address(x))

# functions to filter a results dict by criteria
def filter_by_port(hosts, port):
    result = defaultdict(list)
    for key in hosts.keys():
        for scan in hosts[key]:
            if str(port) in [x['port'] for x in scan['ports']]:
                result[key].append(scan)
    return result

# prefix is a left part of an ip address, eg 192.168
def filter_by_prefix(hosts, prefix):
    result = {}
    for key in hosts.keys():
        if key.startswith(prefix):
            result[key] = hosts[key]
    return result

# makes sense only for nmap scans as the script part creates the service info
def filter_by_service(hosts, service):
    result = defaultdict(list)
    for key in hosts.keys():
        for scan in hosts[key]:
            for port in scan['ports']:
                if 'service' in port and port['service'].startswith(service):
                    result[key].append(scan)
    return result

def filter_by_network(hosts, address, mask):
    if mask == '32' and len(address.split('.')) == 4:
        return {address: hosts[address]}
    filtered = {}
    network = ipaddress.ip_network('%s/%s'%(address,mask))
    for key in hosts.keys():
        if ipaddress.ip_address(key) in network:
            filtered[key] = hosts[key]
    return filtered

def filter_by_having_notes(hosts):
    noted = notes.hostswithcomments()
    filtered = {}
    for key in hosts.keys():
        if key in noted:
            filtered[key] = hosts[key]
    return filtered
        
# useful but not exposed to the UI, I think
# consider having a checkbox for this in the UI
def filter_by_missing_scan(hosts, scantype):
    filtered = {}
    for key in hosts.keys():
        if not scantype in map(lambda x:x['scantype'], hosts[key]):
            filtered[key] = hosts[key]
    return filtered


# horrendous hack
def filter_by_bluekeep(hosts):
    filtered = {}
    for key in hosts.keys():
        for scan in hosts[key]:
            if scan['scantype'] == 'bluekeep'\
                and 'target is vulnerable' in scan['ports'][0]['status']:
                filtered[key] = hosts[key]
                sys.stderr.write(scan['ports'][0]['status'] + '\n')
    return filtered

def filter_by_ms17010(hosts):
    filtered = {}
    for key in hosts.keys():
        for scan in hosts[key]:
            if scan['scantype'] == 'ms17_010'\
                 and 'Host is likely VULNERABLE' in scan['ports'][0]['status']:
                filtered[key] = hosts[key]
                sys.stderr.write(scan['ports'][0]['status'] + '\n')
    return filtered

def filter_by_ms12020(hosts):
    filtered = {}
    for key in hosts.keys():
        for scan in hosts[key]:
            if scan['scantype'] == 'ms12_020'\
                 and 'vulnerable' in scan['ports'][0]['status'].lower() and 'not' not in scan['ports'][0]['status'].lower(): #yolo might work
                filtered[key] = hosts[key]
                sys.stderr.write(scan['ports'][0]['status'] + '\n')
    return filtered

def filter_by_cve_2021_1675(hosts):
    filtered = {}
    for key in hosts.keys():
        for scan in hosts[key]:
            if scan['scantype'] == 'cve_2021_1675'\
                 and 'target is vulnerable' in scan['ports'][0]['status'].lower():
                filtered[key] = hosts[key]
                sys.stderr.write(scan['ports'][0]['status'] + '\n')
    return filtered

def filter_by_vulns(hosts):
    filtered = filter_by_ms17010(hosts)
    filtered.update(filter_by_bluekeep(hosts))
    filtered.update(filter_by_ms12020(hosts))
    filtered.update(filter_by_cve_2021_1675(hosts))
    return filtered
    
def filter_by_screenshots(hosts):
    filtered = {}
    for key in hosts.keys():
        for scan in hosts[key]:
            if 'screenshot' in scan['scantype']:
                filtered[key] = hosts[key]
    return filtered

def filter_by_shares(hosts, readable=False, writable=False):
    filtered = {}
    for key in hosts.keys():
        ss = smbsummary(hosts, key)
        if ss and 'shares' in ss.keys():
            for s in ss['shares']:
                if 'read' in s['permissions'].lower() and readable:
                    filtered[key] = hosts[key]
                if 'write' in s['permissions'].lower() and writable:
                    filtered[key] = hosts[key]
    return filtered
                        

def smbmap_output(hosts, ipaddr):
    ret = []
    for scan in hosts[ipaddr]:
        if scan['scantype'] == 'smbenum':
            smbscan = scan['ports'][0] # it's always a list of length 1
            output = open(smbscan['file'], 'r').read()
            ret.append(output)
    return ret

def smbmap_outputs(hosts):
    outputs = defaultdict(list)
    for key in hosts.keys():
        for scan in hosts[key]:
            if scan['scantype'] == 'smbenum':
                smbscan = scan['ports'][0] # it's always a list of length 1
                output = open(smbscan['file'], 'r').read()
                outputs[key].append(output)
    return outputs

# perly function (i wrote it, won't attempt to read again)
def summary_from_smbscan(scanstr): #scanstr is a string with raw smbscan output
    r = {}
    lines = [x.strip() for x in scanstr.split('\n')]
    lines = filter(lambda x: not 'Working on it' in x, lines)
    shares = []
    insharelist=False

    # SMB         192.168.10.1    445    XXXDS01          Share           Permissions     Remark
    sharenameindex = 0
    permissionsindex = 0
    remarkindex = 0
    for l in lines:
        if 'name:' in l and 'domain:' in l:
            ex=re.compile('.*\[\*\]\s([^(]+)\s\(name:([^)]+)\)\s\(domain:([^)]+)\).*')
            m = ex.match(l)
            if m:
                r['osversion'] = m[1]
                r['name'] = m[2]
                r['domain'] = m[3]
        if l.startswith('Domain Name: '):
            r['domain'] = l.split(': ', 1)[-1].strip()
        if '[+] Enumerated shares' in l:
            insharelist=True
            continue
        if insharelist and 'Permissions' in l:
            sharenameindex = l.index('Share')
            permissionsindex = l.index('Permissions')
            remarkindex = l.index('Remark')
            continue
        if insharelist and 'Permissions' not in l and '------' not in l and '[+]' not in l:
            sharename = l[sharenameindex:permissionsindex-2].strip()
            permissions = l[permissionsindex:remarkindex-2].strip()
            remark = l[remarkindex:].strip()
            share = {'name': sharename,
                     'permissions': permissions,
                     'remark': remark}
            shares.append(share)
            continue
        if '[+] Enumerated' in l:
            break
    if len(shares) > 0:
        r['shares'] = shares
    return r

def smbsummary(hosts, ip):
    smb = smbmap_output(hosts, ip)
    foo = {}
    for x in smb:
        su = summary_from_smbscan(x)
        foo.update(summary_from_smbscan(x))
    return foo


# recursively checks if any string value contains content
def match_leaf(d, content):
    tip = True
    if not dict in map(type, d.values()) and not list in map(type, d.values()):
        tip = False
    for key, val in d.items():
        if type(val) == str and content.lower() in val.lower():
            return True
        if not tip and content.lower() in key.lower():
            return True
        if type(val) == dict:
            return match_leaf(val, content)
        elif type(val) == list:
            return True in map(lambda x:match_leaf(x, content), val)
    return False

# checks if the content is matched by any values in the leaf dicts
# also if the dict doesn't have dicts as values, checks the keys as well
def filter_by_content(hosts, content):
    filtered = {}
    for key in hosts.keys():
        for scan in hosts[key]:
            if match_leaf(scan, content):
                filtered[key] = hosts[key]
    return filtered
    

def get_results(args, filters={}):
    if args[0] == 'ip': # single result by ip, /results/ip/192.168.0.1
        ip = args[1]
        return get_results_for_ip(ip)
    elif args[0] == 'port':
        port = args[1]
        return get_results_by_port(port)
    elif args[0] == 'filter':
        #sys.stderr.write('filtering: prefix=%s port=%s service=%s vulns=%s screenshots=%s\n'%(str(prefix), str(port), str(service), str(vulns), str(screenshots)))
        return get_filtered_results(filters)
    elif args[0] == 'all':
        r = Results()
        r.read_all('results')
        return r.hosts
    elif args[0] == 'networks': # i don't think this is used currently
        r = Results()
        r.read_all('results')
        counts = collections.defaultdict(int)
        for key in r.hosts.keys():
            k = '.'.join(key.split('.')[:2])
            counts[k] += 1
        return dict(counts)
    elif args[0] == 'ips':
        r = Results()
        r.read_all('results')
        return {'ips':sorted_addresses(r.hosts.keys())}
    else:
        return {"status": "not ok"} # what

def latest_only(res):
    bytype = {}
    for x in res:
        t = x['scantype']
        if t in bytype:
            if x['timestamp'] > bytype[t]['timestamp']:
                bytype[t] = x
        else:
            bytype[t] = x
    return list(bytype.values())
#print(json.dumps(res, indent=True, sort_keys=True))
    
def get_results_for_ip(ip):
    r = Results()
    r.read_all('results')
    if ip in r.hosts.keys():
        ret = {ip: latest_only(r.hosts[ip])}
        # postprocess smb results if any
        smb = smbmap_output(r.hosts, ip)
        foo = {}
        for x in smb:
            foo.update(summary_from_smbscan(x))
            #print(json.dumps(foo, indent=4))
        ret[ip].append({'scantype': 'smbinfo',
                        'smbinfo': foo})
        return ret
    else:
        return {}

def get_results_by_port(port):
    r = Results()
    r.read_all('results')
    return r.by_port(port)

def get_filtered_results(filters):
    prefix = filters['prefix']
    port = filters['port']
    service = filters['service']
    vulns = filters['vulns']
    screenshots = filters['screenshots']
    notes = filters['notes']
    r = Results()
    r.read_all('results')
    filtered = r.hosts
    sys.stderr.write('count all=%d\n'%(len(filtered.keys())))
    if prefix and len(prefix) > 0:
        if not prefix[-1] == '.':
            prefix += '.'
        filtered = filter_by_prefix(filtered, prefix)
        sys.stderr.write('count prefix=%d\n'%(len(filtered.keys())))
    if port and len(port) > 0:
        filtered = filter_by_port(filtered, port)
        sys.stderr.write('count port=%d\n'%(len(filtered.keys())))
    if service and len(service) > 0:
        filtered = filter_by_service(filtered, service)
        sys.stderr.write('count service=%d\n'%(len(filtered.keys())))
    if vulns and vulns == 'true':
        filtered = filter_by_vulns(filtered)
        sys.stderr.write('count vulns=%d\n'%(len(filtered.keys())))
    if screenshots and screenshots == 'true':
        filtered = filter_by_screenshots(filtered)
        sys.stderr.write('count screenshots=%d\n'%(len(filtered.keys())))
    if notes and notes == 'true':
        filtered = filter_by_having_notes(filtered)
        sys.stderr.write('count notes=%d\n'%(len(filtered.keys())))
    sys.stderr.write('count final=%d\n'%(len(filtered.keys())))
    return {'ips':sorted_addresses(filtered.keys())}

def get_all_results():
    r = Results()
    r.read_all('results')
    return r.hosts

def list_ips():
    r = Results()
    r.read_all('results')
    return {'ips':sorted_addresses(r.hosts.keys())} # 

def get_attachment(pathcomponents):
    if re_uuid.match(pathcomponents[0]): # some scans save files, this returns them
        filepath = join('results', *pathcomponents)
        if filepath.endswith('.png'):
            self.set_header('content-type', 'image/png')
            return open(filepath,'rb').read()
        if filepath.endswith('.jpg'):
            self.set_header('content-type', 'image/jpg')
            return open(filepath,'rb').read()
        else:
            self.set_header('content-type', 'text/plain')
            return open(filepath,'rb').read()
    else:
        return {"status": "not ok"} # what


class Results:
    def __init__(self):
        self.hosts = defaultdict(list)
        self.scans = [] # masscans and nmaps

    # reads all files in the given path.
    # for backwards compatibility, a nmap/masscan results file can be in the results dir
    # as [uuid].xml
    # everything else should be either [uuid]/output.xml for nmap/masscan
    # and [uuid]/results.json for all other types
    def read_all(self, path, latest_only = False):
        files = [x for x in listdir(path) if isfile(join(path, x)) and x.endswith('.xml')]
        directories = [x for x in listdir(path) if isdir(join(path, x))]
        for f in files:
            j = ScanJob()
            j.load_file(join(path,f))
            for key in j.hosts.keys():
                #print(h)
                self.hosts[key].append(j.hosts[key])
        for d in directories:
            timestamp = os.stat(join(path, d)).st_ctime
            rfile = join(path, d, 'results.json')
            if isfile(rfile):
                r = json.loads(open(rfile,'r').read())
                for entry in r:
                    host = entry['host']
                    port = entry['port']
                    fname = join(path, d, entry['file']) if 'file' in entry else ''
                    scantype = entry['scantype']
                    if scantype == 'ffuf':
                        obj = {'ipv4': host, 'scantype': scantype, 'ports': [{'port': port, 'file': fname, 'results': entry['output']['results']}]}
                    elif scantype in ['bluekeep', 'ms12_020', 'ms17_010', 'cve_2021_1675']:
                        obj = {'ipv4': host, 'scantype': scantype, 'ports': [{'port': port, 'status': entry['status']}]}
                    elif fname == '' or os.stat(fname).st_size == 0:
                        continue
                    else:
                        obj = {'ipv4': host, 'scantype': scantype, 'ports': [{'port': port, 'file': fname}]}
                    obj['timestamp'] = timestamp
                    self.hosts[host].append(obj)
            elif isfile(join(path,d, 'output.xml')):
                j = ScanJob()
                j.load_file(join(path,d, 'output.xml'))
                for key in j.hosts.keys():
                    self.hosts[key].append(j.hosts[key])
            infoname = join(path, d, 'info.json')
            if isfile(infoname):
                info = json.loads(open(infoname, 'r').read())
                ts = datetime.fromtimestamp(stat(infoname).st_mtime, tz=timezone.utc)
                info['timestamp'] = str(ts)
                if info['scantype'] in ['nmap', 'masscan']:
                    try:
                        net = ipaddress.ip_network(info['target']) #just check if it's valid
                        self.scans.append(info)
                    except:
                        pass

    def by_ip(self, ip):
        return self.hosts[ip]

    def by_port(self, port):
        return filter_by_port(self.hosts, port)

    
if __name__=='__main__':
    r = Results()
    r.read_all(sys.argv[1])
    print(json.dumps(r.hosts, indent=4, sort_keys=True))
