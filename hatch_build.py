import os
import shutil
import subprocess
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):

    def initialize(self, version, build_data):
        print(f"[{self.target_name}] Custom build hook initialized. Building frontend...")

        # Root directory is the directory containing hatch_build.py
        root_dir = os.path.abspath(os.path.dirname(__file__))
        web_dir = os.path.join(root_dir, "web")

        is_git_repo = os.path.exists(os.path.join(root_dir, ".git"))
        if is_git_repo:
            # 1. Build the web frontend
            try:
                subprocess.run(["npm", "run", "build"], cwd=web_dir, check=True)
            except Exception as e:
                print(f"Error building frontend: {e}")
                raise e

            # 2. Copy the build output to app/web_build
            src_dir = os.path.join(web_dir, "build")
            dest_dir = os.path.join(root_dir, "app", "web_build")

            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)

            shutil.copytree(src_dir, dest_dir)
            print(f"Copied built frontend files to {dest_dir}")

            # 3. Copy agent/config to app/agent_config
            agent_config_src = os.path.join(root_dir, "agent", "config")
            agent_config_dest = os.path.join(root_dir, "app", "agent_config")

            if os.path.exists(agent_config_dest):
                shutil.rmtree(agent_config_dest)

            shutil.copytree(agent_config_src, agent_config_dest)
            print(f"Copied agent config templates to {agent_config_dest}")
        else:
            print("Not in git repository. Skipping frontend build (using packaged files).")


