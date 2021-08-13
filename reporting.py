#!/usr/bin/env python3

import sys
import results

def report(dir):
    r = results.Results()
    r.read_all(dir)
    print("Hosts vulnerable to Bluekeep:")
    for key in results.sorted_addresses(results.filter_by_bluekeep(r.hosts).keys()):
        print('  %s'%key)
    print('\nHosts vulnerable to MS17-010:')
    for key in results.sorted_addresses(results.filter_by_ms17010(r.hosts).keys()):
        print('  %s'%key)


if __name__=='__main__':
    report(sys.argv[1])
