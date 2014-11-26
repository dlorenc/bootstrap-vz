from bootstrapvz.base import Task
from bootstrapvz.common import phases
from bootstrapvz.common.tasks import boot
from bootstrapvz.common.tasks import initd
from bootstrapvz.common.tools import log_check_call
from bootstrapvz.common.tools import sed_i
from bootstrapvz.providers.gce.tasks import boot as gceboot
import os
import os.path
import shutil
import subprocess
import time

ASSETS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), 'assets'))


class AddDockerDeps(Task):
	description = 'Add packages for docker deps'
	phase = phases.package_installation
	DOCKER_DEPS = ['aufs-tools', 'btrfs-tools', 'git', 'iptables',
	               'procps', 'xz-utils', 'ca-certificates']

	@classmethod
	def run(cls, info):
		for pkg in cls.DOCKER_DEPS:
			info.packages.add(pkg)


class AddDockerBinary(Task):
	description = 'Add docker binary'
	phase = phases.system_modification

	@classmethod
	def run(cls, info):
		docker_version = info.manifest.plugins['docker_daemon'].get('version', False)
		docker_url = 'https://get.docker.io/builds/Linux/x86_64/docker-'
		if docker_version:
			docker_url += docker_version
		else:
			docker_url += 'latest'
		bin_docker = os.path.join(info.root, 'usr/bin/docker')
		log_check_call(['wget', '-O', bin_docker, docker_url])
		os.chmod(bin_docker, 0755)


class AddGenerateCertBinary(Task):
	description = 'Add generate_cert binary'
	phase = phases.system_modification

	@classmethod
	def run(cls, info):
		generate_cert_url = 'https://github.com/SvenDowideit/generate_cert/releases/download/0.1/generate_cert-0.1-linux-amd64'
		bin_generate_cert = os.path.join(info.root, 'usr/bin/generate_cert')
		log_check_call(['wget', '-O', bin_generate_cert, generate_cert_url])
		os.chmod(bin_generate_cert, 0755)


class AddDockerInit(Task):
	description = 'Add docker init script'
	phase = phases.system_modification
	successors = [initd.InstallInitScripts]

	@classmethod
	def run(cls, info):
		init_src = os.path.join(ASSETS_DIR, 'init.d/docker')
		info.initd['install']['docker'] = init_src
		default_src = os.path.join(ASSETS_DIR, 'default/docker')
		default_dest = os.path.join(info.root, 'etc/default/docker')
		shutil.copy(default_src, default_dest)
		docker_opts = info.manifest.plugins['docker_daemon'].get('docker_opts')
		if docker_opts:
			sed_i(default_dest, r'^#*DOCKER_OPTS=.*$', 'DOCKER_OPTS="%s"' % docker_opts)

		if info.manifest.plugins['docker_daemon'].get('tls'):
			# Copy the provider-specific script to export the VM's external IP.
			external_ip_src = os.path.join(ASSETS_DIR, info.manifest.provider['name'], 'external_ip')
			external_ip_dest = os.path.join(info.root, 'etc/default/external_ip')
			shutil.copy(external_ip_src, external_ip_dest)


class EnableMemoryCgroup(Task):
	description = 'Change grub configuration to enable the memory cgroup'
	phase = phases.system_modification
	successors = [boot.InstallGrub_1_99, boot.InstallGrub_2]
	predecessors = [boot.ConfigureGrub, gceboot.ConfigureGrub]

	@classmethod
	def run(cls, info):
		grub_config = os.path.join(info.root, 'etc/default/grub')
		sed_i(grub_config, r'^(GRUB_CMDLINE_LINUX*=".*)"\s*$', r'\1 cgroup_enable=memory"')


class PullDockerImages(Task):
	description = 'Pull docker images'
	phase = phases.system_modification
	predecessors = [AddDockerBinary]

	@classmethod
	def run(cls, info):
		from bootstrapvz.common.exceptions import TaskError
		images = info.manifest.plugins['docker_daemon'].get('pull_images', [])
		retries = info.manifest.plugins['docker_daemon'].get('pull_images_retries', 10)

		bin_docker = os.path.join(info.root, 'usr/bin/docker')
		graph_dir = os.path.join(info.root, 'var/lib/docker')
		socket = 'unix://' + os.path.join(info.workspace, 'docker.sock')
		pidfile = os.path.join(info.workspace, 'docker.pid')

		try:
			# start docker daemon temporarly.
			daemon = subprocess.Popen([bin_docker, '-d', '--graph', graph_dir, '-H', socket, '-p', pidfile])
			# wait for docker daemon to start.
			for _ in range(retries):
				if log_check_call([bin_docker, '-H', socket, 'version']) == 0:
					break
				time.sleep(1)
			for img in images:
				# docker load if tarball.
				if img.endswith('.tar.gz') or img.endswith('.tgz'):
					cmd = [bin_docker, '-H', socket, 'load', '-i', img]
					if log_check_call(cmd) != 0:
						msg = 'error loading docker image {img}.'.format(img=img)
						raise TaskError(msg)
				# docker pull if image name.
				else:
					cmd = [bin_docker, '-H', socket, 'pull', img]
					if log_check_call(cmd) != 0:
						msg = 'error pulling docker image {img}.'.format(img=img)
						raise TaskError(msg)
		finally:
			# shutdown docker daemon.
			daemon.terminate()
