"""P1-8: Dual execution backends — LocalExecutor and DockerExecutor."""

import subprocess
from abc import ABC, abstractmethod


class BaseExecutor(ABC):
    """Abstract execution backend."""

    @abstractmethod
    def run(self, command: str, working_dir: str = "",
            timeout: int = 30, env: dict = None) -> tuple[bool, str, int]:
        """Execute a command. Returns (ok, output, exit_code)."""
        pass


class LocalExecutor(BaseExecutor):
    """Execute commands directly on the host via subprocess.

    Optionally wraps in systemd-run --scope for resource isolation.
    """

    def __init__(self, use_systemd: bool = False):
        self.use_systemd = use_systemd

    def run(self, command: str, working_dir: str = "",
            timeout: int = 30, env: dict = None) -> tuple[bool, str, int]:
        if self.use_systemd:
            return self._systemd_run(command, working_dir, timeout, env)
        return self._subprocess_run(command, working_dir, timeout, env)

    def _subprocess_run(self, command, working_dir, timeout, env):
        try:
            result = subprocess.run(
                command, shell=True,
                cwd=working_dir if working_dir else None,
                capture_output=True, text=True, timeout=timeout,
                env=env,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            return result.returncode == 0, output, result.returncode
        except subprocess.TimeoutExpired:
            return False, f"Timed out after {timeout}s", -1
        except Exception as e:
            return False, str(e), -1

    def _systemd_run(self, command, working_dir, timeout, env):
        """Wrap command in systemd-run --scope for cgroup isolation."""
        cmd = f"systemd-run --user --scope --quiet -- bash -c {_quote(command)}"
        return self._subprocess_run(cmd, working_dir, timeout, env)


class DockerExecutor(BaseExecutor):
    """Execute commands inside a Docker container.

    Maps /host to the host filesystem so commands can access host paths.
    """

    def __init__(self, image: str = "ubuntu:latest"):
        self.image = image

    def run(self, command: str, working_dir: str = "",
            timeout: int = 30, env: dict = None) -> tuple[bool, str, int]:
        wd = f"/host/{working_dir}" if working_dir else "/host"
        env_args = " ".join(f"-e {k}={v}" for k, v in (env or {}).items())
        docker_cmd = (
            f"docker run --rm {env_args} -v /:/host -w {wd} "
            f"{self.image} bash -c {_quote(command)}"
        )
        try:
            result = subprocess.run(
                docker_cmd, shell=True,
                capture_output=True, text=True, timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            return result.returncode == 0, output, result.returncode
        except subprocess.TimeoutExpired:
            return False, f"Timed out after {timeout}s", -1
        except Exception as e:
            return False, str(e), -1


def get_executor(step: dict, skill: dict) -> BaseExecutor:
    """Select executor based on step/skill configuration.

    Priority: step-level > skill-level > default (LocalExecutor).
    """
    executor_name = step.get("executor") or skill.get("executor", "local")
    if executor_name == "docker":
        image = step.get("image") or skill.get("image", "ubuntu:latest")
        return DockerExecutor(image=image)
    systemd = step.get("use_systemd", skill.get("use_systemd", False))
    return LocalExecutor(use_systemd=systemd)


def _quote(s: str) -> str:
    """Safe shell quoting."""
    import shlex
    return shlex.quote(s)
