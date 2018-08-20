#!/usr/bin/env python3

import os
import toml
import formic
import shlex
import subprocess
import collections
from subprocess import DEVNULL
from datetime import datetime
from pathlib import Path
from appdirs import AppDirs
from pprint import pprint
from . import readers, utils
import platform
import sys

app = AppDirs('exmemo')

class Workspace:

    @classmethod
    def from_dir(cls, dir, strict=True):
        """
        Create a workspace object containing given directory (or the current
        working directory, by default).  This involves descending the directory
        hierarchy looking for the root of the project, which should contain a
        characteristic set of files and directories.
        """

        dir = given_dir = Path(dir)
        work = given_work = Workspace(dir)

        while not work.has_project_files:
            dir = dir.parent
            if dir == Path('/'):
                if strict: raise WorkspaceNotFound(given_dir)
                else: return given_work
            else:
                work = Workspace(dir)

        return work

    @classmethod
    def from_cwd(cls, strict=True):
        """
        Create a workspace object containing given directory (or the current
        working directory, by default).  This involves descending the directory
        hierarchy looking for the root of the project, which should contain a
        characteristic set of files and directories.
        """
        return cls.from_dir(cls.get_cwd(), strict)

    @staticmethod
    def get_cwd():
        """
        Return the current working directory, making an effort to keep the path
        nice and short by not resolving symlinks.
        """
        # Most shells will set `$PWD` with the path the user sees the current
        # working directory as, taking into account whichever symlinks they
        # used to get there.  So this is the path we want to use, if it's
        # available.  If it's not, fall back on `os.getcwd()`, which will give
        # us the current working directory without any symlinks.
        return Path(os.getenv('PWD', os.getcwd()))

    def __init__(self, root):
        # Use `os.path.abspath()` instead of `Path.resolve()` to avoid
        # resolving any symlinks in the path.
        self._root = Path(os.path.abspath(root))

        if not self.config_paths:
            self._config = {}
        else:
            self._config = toml.load([str(x) for x in self.config_paths])

    @property
    def root_dir(self):
        return self._root

    @property
    def config(self):
        return self._config

    @property
    def config_paths(self):
        paths = [self.site_config_path, self.user_config_path, self.rcfile]
        return [x for x in paths if x.exists()]

    @property
    def site_config_path(self):
        return Path(app.site_config_dir) / 'conf.toml'

    @property
    def user_config_path(self):
        return Path(app.user_config_dir) / 'conf.toml'

    @property
    def project_config_path(self):
        return self.root_dir / '.exmemorc'

    rcfile = project_config_path

    @property
    def analysis_dir(self):
        return self.root_dir / 'analysis'

    @property
    def data_dir(self):
        return self.root_dir / 'data'

    @property
    def documents_dir(self):
        return self.root_dir / 'documents'

    @property
    def notebook_dir(self):
        return self.root_dir / 'notebook'

    @property
    def protocols_dir(self):
        return self.root_dir / 'protocols'

    @property
    def protocols_dirs(self):
        local_dirs = [Path('.'), self.protocols_dir]
        shared_dirs = [Path(x).expanduser() for x in self.config.get('shared_protocols', [])]
        return [x for x in local_dirs + shared_dirs if x.exists()]

    @property
    def has_project_files(self):
        if not self.rcfile.exists():
            return False
        if not self.analysis_dir.exists():
            return False
        if not self.data_dir.exists():
            return False
        if not self.documents_dir.exists():
            return False
        if not self.notebook_dir.exists():
            return False
        if not self.protocols_dir.exists():
            return False
        return True

    def iter_data(self, substr=None):
        return iter_paths_matching_substr(self.data_dir, substr)

    def iter_experiments(self, substr=None):
        yield from (x.parent for x in iter_paths_matching_substr(
            self.notebook_dir, substr, f'/{8*"[0-9]"}_{{0}}/{{0}}.rst'))

    def iter_notebook_entries(self, substr=None):
        yield from (x for x in iter_paths_matching_substr(
            self.notebook_dir, substr, '/{}.rst'))
        yield from (x for x in iter_paths_matching_substr(
            self.notebook_dir, substr, f'/{8*"[0-9]"}_{{0}}/{{0}}.rst'))

    def iter_protocols(self, substr=None):
        for dir in self.protocols_dirs:
            yield from iter_paths_matching_substr(dir, substr)

    def iter_protocols_from_dir(self, dir, substr=None):
        yield from iter_paths_matching_substr(dir, substr)

    def pick_path(self, substr, paths, default=None, no_choices=None):
        paths = list(paths)

        if len(paths) == 0:
            raise no_choices or CantMatchSubstr('choices', substr)

        if len(paths) == 1:
            return paths[0]

        if substr is None:
            # If the user provided a default, return it.
            if default is not None:
                return default

            # If the current working directory is one of the paths, return it.
            cwd = self.get_cwd()
            resolved_paths = [x.resolve() for x in paths]
            if cwd.resolve() in resolved_paths:
                return cwd

            # Otherwise just return the last path, which will be the most
            # recent if the paths are prefixed by date and sorted.
            return paths[-1]

        # Once I've written the config-file system, there should be an option
        # to change how this works (i.e. CLI vs GUI vs automatic choice).
        i = utils.pick_one(x.name for x in paths)
        return paths[i]

    def pick_data(self, substr):
        return self.pick_path(
                substr, self.iter_data(substr),
                no_choices=CantMatchSubstr('data files', substr),
        )

    def pick_experiment(self, substr):
        return self.pick_path(
                substr, self.iter_experiments(substr),
                no_choices=CantMatchSubstr('experiments', substr),
        )

    def pick_notebook_entry(self, substr):
        return self.pick_path(
                substr, self.iter_notebook_entries(substr),
                no_choices=CantMatchSubstr('notebook entries', substr),
        )

    def pick_protocol(self, substr):
        return self.pick_path(
                substr, self.iter_protocols(substr),
                no_choices=CantMatchSubstr('protocols', substr),
        )

    def pick_protocol_reader(self, path, args):
        path = Path(path)
        return readers.pick_reader(path, args)

    def init_project(self, title):
        from cookiecutter.main import cookiecutter
        from .cookiecutter import cookiecutter_path
        from click.exceptions import Abort

        url = self.config.get('cookiecutter_url') or cookiecutter_path

        try:
            cookiecutter(str(url), extra_context={
                    'project_title': title,
                    'year': datetime.today().year,
            })
        except Abort:
            raise KeyboardInterrupt

    def init_experiment(self, title):
        slug = slug_from_title(title)
        expt = self.notebook_dir / f'{utils.ymd()}_{slug}'
        # Use of str for Windows paths
        rst = str(expt / f'{slug}.rst')

        if not os.path.isdir(expt):
            expt.mkdir()
        else:
            sys.exit('Experiment exists. Use exmemo [note] edit [<substr>].')

        with rst.open('w') as file:
            file.write(f"""\
{'*' * len(title)}
{title}
{'*' * len(title)}
""")
        self.launch_editor(rst)

    def launch_editor(self, path):
        if os.environ.get('EDITOR') is None:
            if platform.system() == "Windows":
                editor = 'write.exe'
            else:
                editor = 'vim'
        else:
            editor = self.config.get('editor', os.environ.get('EDITOR'))
        cmd = *shlex.split(editor), path
        subprocess.Popen(cmd)

    def launch_terminal(self, dir):
        term = self.config.get('terminal', os.environ.get('TERMINAL')) or 'xterm'
        cmd = shlex.split(term)
        subprocess.Popen(cmd, cwd=dir, stdout=DEVNULL, stderr=DEVNULL)

    def launch_pdf(self, path):
        pdf = self.config.get('pdf', os.environ.get('PDF')) or 'evince'
        cmd = *shlex.split(pdf), path
        subprocess.Popen(cmd)

    def launch_browser(self, url, new_window=False):
        browser = self.config.get('browser', os.environ.get('BROWSER')) or 'firefox'
        new_window_flag = self.config.get('browser_new_window_flag', os.environ.get('BROWSER_NEW_WINDOW_FLAG')) or '--new-window'

        if new_window:
            cmd = *shlex.split(browser), *shlex.split(new_window_flag), url
        else:
            cmd = *shlex.split(browser), url

        subprocess.Popen(cmd)

    def sync_data(self, verbose):
        from . import collectors
        collectors.sync_data(self, verbose)

    def build_notebook(self, force=False):
        if force:
            make = 'make', 'clean', 'html'
        else:
            make = 'make', 'html'

        subprocess.run(make, cwd=self.notebook_dir)

    def get_notebook_entry(self, dir):
        dir = Path(dir)
        date, slug = dir.name.split('_', 1)
        return dir / f"{slug}.rst"

    def get_data_collectors():
        pass


class WorkspaceNotFound(IOError):
    show_message_and_die = True

    def __init__(self, dir):
        self.message = f"'{dir}' is not a workspace."


class CantMatchSubstr(Exception):
    show_message_and_die = True

    def __init__(self, type, substr):
        self.message = f"No {type} matching '{substr}'."



def slug_from_title(title):

    class Sanitizer: #
        def __getitem__(self, ord): #
            char = chr(ord)
            if char.isalnum(): return char.lower()
            if char in ' _-': return '_'
            # Any other character gets dropped.

    return title.translate(Sanitizer())

def iter_paths_matching_substr(dir, substr=None, glob=None, exclude=['.*'], symlinks=True, include_origin=False):
    substr = '*' if substr is None else f'*{substr}*'
    substr = substr.replace('/', '*/**/*')

    if glob is None:
        include = [f'**/{substr}', f'**/{substr}/**']  # Match files and dirs.
    else:
        include = glob.format(substr)

    matches = sorted(
            Path(x) for x in formic.FileSet(
                directory=dir,
                include=include,
                exclude=exclude,
                symlinks=symlinks,
            )
    )

    if include_origin:
        yield from ((dir, x) for x in matches)
    else:
        yield from matches


