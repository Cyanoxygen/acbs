import logging
import logging.handlers
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Tuple

from acbs import __version__
from acbs.const import CONF_DIR, DUMP_DIR, TMP_DIR, LOG_DIR
from acbs.deps import tarjan_search
from acbs.fetch import fetch_source, process_source
from acbs.find import find_package, check_package_groups
from acbs.parser import get_tree_by_name, get_deps_graph
from acbs.pm import install_from_repo
from acbs.utils import invoke_autobuild, guess_subdir, full_line_banner, print_package_names, make_build_dir, \
    print_build_timings, has_stamp, ACBSLogFormatter


class BuildCore(object):

    def __init__(self, args) -> None:
        self.debug = args.debug
        self.no_deps = args.no_deps
        self.dl_only = args.get
        self.tree = args.acbs_tree or 'default'
        self.build_queue = args.packages
        self.tree_dir = ''
        # static vars
        self.conf_dir = CONF_DIR
        self.dump_dir = DUMP_DIR
        self.tmp_dir = TMP_DIR
        self.log_dir = LOG_DIR
        self.init()

    def init(self) -> None:
        sys.excepthook = self.acbs_except_hdr
        print(full_line_banner(
            'Welcome to ACBS - {}'.format(__version__)))
        if self.debug:
            log_verbosity = logging.DEBUG
        else:
            log_verbosity = logging.INFO
        try:
            for directory in [self.dump_dir, self.tmp_dir, self.conf_dir,
                              self.log_dir]:
                if not os.path.isdir(directory):
                    os.makedirs(directory)
        except Exception:
            raise IOError('\033[93mFailed to create work directories\033[0m!')
        self.__install_logger(log_verbosity)
        forest_file = os.path.join(self.conf_dir, 'forest.conf')
        if os.path.exists(forest_file):
            self.tree_dir = get_tree_by_name(forest_file, self.tree)
            if not self.tree_dir:
                raise ValueError('Tree not found!')
        else:
            raise Exception('forest.conf not found')

    def __install_logger(self, str_verbosity=logging.INFO,
                         file_verbosity=logging.DEBUG):
        logger = logging.getLogger()
        logger.setLevel(0)  # Set to lowest to bypass the initial filter
        str_handler = logging.StreamHandler()
        str_handler.setLevel(str_verbosity)
        str_handler.setFormatter(ACBSLogFormatter())
        logger.addHandler(str_handler)
        log_file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(self.log_dir, 'acbs-build.log'), mode='a', maxBytes=2e5, backupCount=3)
        log_file_handler.setLevel(file_verbosity)
        log_file_handler.setFormatter(logging.Formatter(
            '%(asctime)s:%(levelname)s:%(message)s'))
        logger.addHandler(log_file_handler)

    def build(self) -> None:
        packages = []
        build_timings: List[Tuple[str, float]] = []
        error = False
        # begin finding and resolving dependencies
        logging.info('Searching and resolving dependencies...')
        for i in self.build_queue:
            logging.debug('Finding {}...'.format(i))
            package = find_package(i, self.tree_dir)
            if not package:
                raise RuntimeError('Could not find package {}'.format(i))
            packages.extend(package)
        if not self.no_deps:
            logging.debug('Converting queue into adjacency graph...')
            graph = get_deps_graph(packages)
            logging.debug('Running Tarjan search...')
            resolved = tarjan_search(graph, self.tree_dir)
        else:
            logging.warning('Warning: Dependency resolution disabled!')
            resolved = [[package] for package in packages]
        # print a newline
        print()
        packages.clear()  # clear package list for the search results
        # here we will check if there is any loop in the dependency graph
        for dep in resolved:
            if len(dep) > 1:
                # this is a SCC, aka a loop
                logging.error('Found a loop in the dependency graph: {}'.format(
                    print_package_names(dep)))
                error = True
            elif not error:
                packages.extend(dep)
        if error:
            raise RuntimeError(
                'Dependencies NOT resolved. Couldn\'t continue!')
        check_package_groups(packages)
        logging.info(
            'Dependencies resolved, {} packages in the queue'.format(len(resolved)))
        logging.debug('Queue: {}'.format(packages))
        logging.info('Packages to be built: {}'.format(
            print_package_names(packages, 5)))
        # build process
        for task in packages:
            logging.info('Building {}...'.format(task.name))
            source_name = task.name
            if task.base_slug:
                source_name = os.path.basename(task.base_slug)
            if not has_stamp(task.build_location):
                fetch_source(task.source_uri, self.dump_dir, source_name)
            if self.dl_only:
                continue
            if not task.build_location:
                build_dir = make_build_dir(self.tmp_dir)
                task.build_location = build_dir
                process_source(task, source_name)
            else:
                # First sub-package in a meta-package
                if not has_stamp(task.build_location):
                    process_source(task, source_name)
                    Path(os.path.join(task.build_location, '.acbs-stamp')).touch()
                build_dir = task.build_location
            if task.source_uri.subdir:
                build_dir = os.path.join(build_dir, task.source_uri.subdir)
            else:
                subdir = guess_subdir(build_dir)
                if not subdir:
                    raise RuntimeError(
                        'Could not determine sub-directory, please specify manually.')
                build_dir = os.path.join(build_dir, subdir)
            if task.installables:
                logging.info('Installing dependencies from repository...')
                install_from_repo(task.installables)
            start = time.monotonic()
            try:
                invoke_autobuild(task, build_dir)
            except Exception as ex:
                # early printing of build summary before exploding
                if build_timings:
                    print_build_timings(build_timings)
                raise RuntimeError('Error when building {}.\nBuild folder: {}'.format(task.name, build_dir))
            build_timings.append((task.name, time.monotonic() - start))
        print_build_timings(build_timings)

    def acbs_except_hdr(self, type, value, tb):
        logging.debug('Traceback:\n' + ''.join(traceback.format_tb(tb)))
        if self.debug:
            sys.__excepthook__(type, value, tb)
        else:
            print()
            logging.fatal('Oops! \033[93m%s\033[0m: \033[93m%s\033[0m' % (
                str(type.__name__), str(value)))
