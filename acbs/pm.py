from typing import List, Optional, Dict, Tuple, Deque
from acbs.base import ACBSPackageInfo
import logging
import subprocess
import re


installed_cache: List[str] = []
available_cache: List[str] = []


def filter_dependencies(package: ACBSPackageInfo) -> ACBSPackageInfo:
    installables = []
    deps = []
    for dep in package.deps:
        if check_if_installed(dep):
            continue
        if check_if_available(dep):
            installables.append(dep)
            continue
        deps.append(dep)
    package.deps = deps
    package.installables = installables
    return package


def escape_package_name(name: str) -> str:
    return re.sub(r'([+*?])', '\\\\\\1', name)


def fix_pm_states(escaped: List[str]):
    count = 0
    while count < 3:
        try:
            subprocess.check_call(['apt-get', 'install', '-yf'])
            command = ['apt-get', 'install', '-y']
            command.extend(escaped)
            subprocess.check_call(command)
            return
        except Exception:
            count += 1
            continue
    raise RuntimeError('Unable to correct package manager states...')


def check_if_installed(name: str) -> bool:
    logging.debug('Checking if %s is installed' % name)
    try:
        subprocess.check_output(['dpkg', '-s', name], stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError:
        return False


def check_if_available(name: str) -> bool:
    logging.debug('Checking if %s is available' % name)
    try:
        subprocess.check_output(['apt-cache', 'show', escape_package_name(name)], stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError:
        return False


def install_from_repo(packages: List[str]):
    logging.debug('Installing %s' % packages)
    escaped = []
    for package in packages:
        escaped.append(escape_package_name(package))
    command = ['apt-get', 'install', '-y']
    command.extend(escaped)
    try:
        subprocess.check_call(command)
    except subprocess.CalledProcessError:
        logging.warning('Failed to install dependencies, attempting to correct issues...')
        fix_pm_states(escaped)
    return