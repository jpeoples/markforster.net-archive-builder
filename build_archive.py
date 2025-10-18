import os
import json
import requests

import argparse
import time

class Tic:
    def __init__(self):
        self.tic()

    def get_time(self):
        return time.perf_counter_ns()

    def process_diff(self, diff):
        return diff / 1e9

    def tic(self):
        self._last = self.get_time()
    
    def toc(self):
        diff = self.get_time() - self._last
        return self.process_diff(diff)


def make_parser(f=None):
    parser = argparse.ArgumentParser()
    if f:
        f(parser)
    subparsers = parser.add_subparsers()
    return parser, subparsers


class _EntryPoint:
    def __init__(self, f):
        self.f = f
        self._parser = None
        self.name = f.__name__

        f.parser = self.parser


    def prepare_parser(self, parser, subparsers):
        parser = subparsers.add_parser(self.name)
        if self._parser:
            self._parser(parser)

        parser.set_defaults(cmd=self.f)

    def parser(self, f):
        self._parser = f
        return f

class EntryPoints:
    def __init__(self):
        self.entrypoints = []
        self.parser_functions = []

    def common_parser(self, parser):
        for pf in self.parser_functions:
            pf(parser)

    def point(self, f):
        ep =  _EntryPoint(f)
        self.entrypoints.append(ep)
        return f

    def add_common_parser(self, f):
        self.parser_functions.append(f)
        return f
    

    def parse_args(self):
        parser, subparsers = make_parser(self.common_parser)
        for ep in self.entrypoints:
            ep.prepare_parser(parser, subparsers)

        args = parser.parse_args()
        return args

    def main(self):
        args = self.parse_args()
        tic = Tic()
        args.cmd(args)
        tdiff = tic.toc()
        print(f"Ran in {tdiff:0.05f} seconds")



def download_file(url, out_file):
    content = requests.get(url, stream=True).content
    with open(out_file, "wb") as f:
        f.write(content)

def load_json(file="conf.json"):
    with open(file, encoding='utf8') as f:
        return json.load(f)

class DataStore:
    def __init__(self, conf):
        self.conf = conf
        self.root = conf['root']
        self.raw_archive = os.path.join(self.root, conf['local.storage']['raw'])
        os.makedirs(self.raw_archive, exist_ok=True)

    def update_archive(self):
        remote_files = self.conf['remote.raw_files']
        local_files = self.conf['local.raw_files']

        for f in remote_files:
            local = os.path.join(self.raw_archive, local_files[f])
            download_file(remote_files[f], local)

    def load_raw_file(self, f):
        path = os.path.join(self.raw_archive, self.conf['local.raw_files'][f])
        data = load_json(path)
        return data


# Instantiate an EntryPoints object
entry = EntryPoints()

@entry.point
def update_archive(args):
    conf = load_json(args.conf)
    DataStore(conf).update_archive()

@entry.point
def dump_item(args):
    conf = load_json(args.conf)
    ds = DataStore(conf)
    data = ds.load_raw_file('blog')
    print(len(data['posts']))
    data = ds.load_raw_file('general_forum')
    print(len(data['topics']))
    data = ds.load_raw_file('fvp_forum')
    print(len(data['topics']))

@entry.add_common_parser
def common_settings(parser):
    parser.add_argument("--conf", default='conf.json')



if __name__=="__main__":
    entry.main()