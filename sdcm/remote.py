import getpass
import time

import fabric.api
import fabric.network
import fabric.operations
import fabric.tasks


class CmdError(Exception):

    def __init__(self, command=None, result=None, additional_text=None):
        self.command = command
        self.result = result
        self.additional_text = additional_text

    def __str__(self):
        if self.result is not None:
            if self.result.interrupted:
                return "Command %s interrupted by user (Ctrl+C)" % self.command
            if self.result.exit_status is None:
                msg = "Command '%s' failed and is not responding to signals"
                msg %= self.command
            else:
                msg = "Command '%s' failed (rc=%d)"
                msg %= (self.command, self.result.exit_status)
            if self.additional_text:
                msg += ", " + self.additional_text
            return msg
        else:
            return "CmdError"


class AuthenticationError(Exception):

    def __init__(self, msg):
        self.fabric_msg = msg

    def __str__(self):
        return ('Auth error (wrong credentials, '
                'or paramiko bug): {}'.format(self.fabric_msg))


class CmdResult(object):

    """
    Command execution result.

    :param command: String containing the command line itself
    :param exit_status: Integer exit code of the process
    :param stdout: String containing stdout of the process
    :param stderr: String containing stderr of the process
    :param duration: Elapsed wall clock time running the process
    """

    def __init__(self, command="", stdout="", stderr="",
                 exit_status=None, duration=0):
        self.command = command
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.interrupted = False

    def __repr__(self):
        cmd_rep = ("Command: %s\n"
                   "Exit status: %s\n"
                   "Duration: %s\n"
                   "Stdout:\n%s\n"
                   "Stderr:\n%s\n" % (self.command, self.exit_status,
                                      self.duration, self.stdout, self.stderr))
        if self.interrupted:
            cmd_rep += "Command interrupted by user (Ctrl+C)\n"
        return cmd_rep


def update_fabric_env(method):
    """
    Update fabric env with the appropriate parameters.

    :param method: Remote method to wrap.
    :return: Wrapped method.
    """
    def wrapper(*args, **kwargs):
        print('Updating fabric env: ssh -i {} -p {} '
              '{}@{}'.format(args[0].key_filename,
                             args[0].port,
                             args[0].username,
                             args[0].hostname))
        fabric.api.env.update(host_string=args[0].hostname,
                              user=args[0].username,
                              key_filename=args[0].key_filename,
                              port=args[0].port)
        return method(*args, **kwargs)
    return wrapper


def run(command, ignore_status=False, quiet=False):
    result = CmdResult()
    start_time = time.time()
    # Fabric sometimes returns NetworkError even when timeout not reached
    fabric_result = None
    fabric_exception = None

    while True:
        try:
            fabric_result = fabric.operations.run(command=command,
                                                  quiet=quiet,
                                                  warn_only=True,
                                                  timeout=1000000)
            break
        except (fabric.network.NetworkError, AuthenticationError), details:
            print('_run: {}'.format(details))
            fabric_exception = details

    end_time = time.time()
    duration = end_time - start_time
    result.command = command
    result.duration = duration
    if fabric_result is None:
        result.stdout = 'Unable to get stdout ({})'.format(fabric_exception)
        result.stderr = 'Unable to get stderr ({})'.format(fabric_exception)
        result.exit_status = -300
        result.failed = True
        result.succeeded = False
    else:
        result.stdout = str(fabric_result)
        result.stderr = fabric_result.stderr
        result.exit_status = fabric_result.return_code
        result.failed = fabric_result.failed
        result.succeeded = fabric_result.succeeded
    if not ignore_status:
        if result.failed:
            raise CmdError(command=command, result=result)
    return result


class Remote(object):

    INFINITY = 1000000

    """
    Performs remote operations.
    """

    def __init__(self, hostname, username=None, password=None,
                 key_filename=None, port=22, quiet=False):
        """
        Creates an instance of :class:`Remote`.

        :param hostname: the hostname.
        :param username: the username. Default: autodetect.
        :param password: the password. Default: try to use public key.
        :param key_filename: path to an identity file (Example: .pem files
            from Amazon EC2).
        :param quiet: performs quiet operations. Default: True.
        """
        self.hostname = hostname
        if username is None:
            username = getpass.getuser()
        self.username = username
        # None = use public key
        self.password = password
        self.port = port
        self.quiet = quiet
        self.key_filename = key_filename
        fabric.api.env.update(host_string=hostname,
                              user=username,
                              password=password,
                              key_filename=key_filename,
                              port=port,
                              timeout=10,
                              connection_attempts=self.INFINITY,
                              linewise=True,
                              abort_on_prompts=True,
                              abort_exception=AuthenticationError)

    def run(self, command, ignore_status=False):
        """
        Run a remote command.

        :param command: the command string to execute.
        :param ignore_status: Whether to not raise exceptions in case the
            command's return code is different than zero.

        :return: the result of the remote program's execution.
        :rtype: :class:`avocado.utils.process.CmdResult`.
        """
        return_dict = fabric.tasks.execute(self._run, command, ignore_status,
                                           hosts=[self.hostname])
        return return_dict[self.hostname]

    def run_quiet(self, command, ignore_status=False):
        """
        Run a remote command.

        :param command: the command string to execute.
        :param ignore_status: Whether to not raise exceptions in case the
            command's return code is different than zero.

        :return: the result of the remote program's execution.
        :rtype: :class:`avocado.utils.process.CmdResult`.
        """
        with fabric.api.quiet():
            return_dict = fabric.tasks.execute(self._run, command,
                                               ignore_status,
                                               hosts=[self.hostname])
            return return_dict[self.hostname]

    @update_fabric_env
    def _run(self, command, ignore_status=False):
        return run(command=command, ignore_status=ignore_status,
                   quiet=self.quiet)

    def uptime(self):
        """
        Performs uptime (good to check connection).

        :return: the uptime string or empty string if fails.
        """
        res = self.run('uptime', ignore_status=True)
        if res.exit_status == 0:
            return res
        else:
            return ''

    def makedir(self, remote_path):
        """
        Create a directory.

        :param remote_path: the remote path to create.
        """
        self.run('mkdir -p %s' % remote_path)

    def send_files(self, local_path, remote_path):
        result_dict = fabric.tasks.execute(self._send_files, local_path,
                                           remote_path, hosts=[self.hostname])
        return result_dict[self.hostname]

    @update_fabric_env
    def _send_files(self, local_path, remote_path):
        """
        Send files to remote.

        :param local_path: the local path.
        :param remote_path: the remote path.
        """
        try:
            fabric.operations.put(local_path, remote_path,
                                  mirror_local_mode=True)
        except (ValueError, AuthenticationError), details:
            print('_send_files: {}'.format(details))
            return False
        return True

    def receive_files(self, local_path, remote_path):
        result_dict = fabric.tasks.execute(self._receive_files, local_path,
                                           remote_path, hosts=[self.hostname])
        return result_dict[self.hostname]

    @update_fabric_env
    def _receive_files(self, local_path, remote_path):
        """
        receive remote files.

        :param local_path: the local path.
        :param remote_path: the remote path.
        """
        try:
            fabric.operations.get(remote_path,
                                  local_path)
        except (ValueError, AuthenticationError), details:
            print('_receive_files: {}'.format(details))
            return False
        return True
