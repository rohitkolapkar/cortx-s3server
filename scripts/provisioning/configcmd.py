#!/usr/bin/env python3
#
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#

import sys
import os
import time
import errno
import shutil
import math
import fcntl
import glob
import uuid
from pathlib import Path
from  ast import literal_eval
from os import path
from s3confstore.cortx_s3_confstore import S3CortxConfStore
from setupcmd import SetupCmd, S3PROVError
from cortx.utils.process import SimpleProcess
from s3msgbus.cortx_s3_msgbus import S3CortxMsgBus
from s3backgrounddelete.cortx_s3_config import CORTXS3Config
from s3backgrounddelete.cortx_s3_constants import MESSAGE_BUS
from s3_haproxy_config import S3HaproxyConfig
from ldapaccountaction import LdapAccountAction
from s3cipher.cortx_s3_cipher import CortxS3Cipher
from cortx.utils.log import Log
import xml.etree.ElementTree as ET

class ConfigCmd(SetupCmd):
  """Config Setup Cmd."""
  name = "config"

  def __init__(self, config: str, services: str = None):
    """Constructor."""
    try:
      super(ConfigCmd, self).__init__(config, services)
      self.setup_type = self.get_confvalue_with_defaults('CONFIG>CONFSTORE_SETUP_TYPE')
      Log.info(f'Setup type : {self.setup_type}')
      self.cluster_id = self.get_confvalue_with_defaults('CONFIG>CONFSTORE_CLUSTER_ID_KEY')
      Log.info(f'Cluster  id : {self.cluster_id}')
      self.base_config_file_path = self.get_confvalue_with_defaults('CONFIG>CONFSTORE_BASE_CONFIG_PATH')
      Log.info(f'config file path : {self.base_config_file_path}')
      self.base_log_file_path = self.get_confvalue_with_defaults('CONFIG>CONFSTORE_BASE_LOG_PATH')
      Log.info(f'log file path : {self.base_log_file_path}')

    except Exception as e:
      raise S3PROVError(f'exception: {e}')

  def process(self, *args, **kwargs):
    lock_directory = os.path.join(self.base_config_file_path,"s3")
    if not os.path.isdir(lock_directory):
      try:
         os.mkdir(lock_directory)
      except BaseException:
         Log.error("Unable to create lock_directory directory ")
    lockfile = path.join(lock_directory, 's3_setup.lock')
    Log.info(f'Acquiring the lock at {lockfile}...')
    with open(lockfile, 'w') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      Log.info(f'acquired the lock at {lockfile}.')
      self.process_under_flock(*args, **kwargs)
    # lock and file descriptor released automatically here.
    Log.info(f'released the lock at {lockfile}.')

  def process_under_flock(self):
    """Main processing function."""
    Log.info(f"Processing phase = {self.name}, config = {self.url}, service = {self.services}")
    Log.info("validations started")
    self.phase_prereqs_validate(self.name)
    self.phase_keys_validate(self.url, self.name)
    Log.info("validations completed")

    try:
      Log.info("common config started")
      self.process_common()
      Log.info("common config completed")
      # Do not change sequence of the services as it is mentioned as per dependencies.
      if self.service_haproxy in self.services:
        Log.info("haproxy config started")
        self.process_haproxy()
        Log.info("haproxy config completed")
      if self.service_s3server in self.services:
        Log.info("s3server config started")
        self.process_s3server()
        Log.info("s3server config completed")
      if self.service_authserver in self.services:
        Log.info("authserver config started")
        self.process_authserver()
        Log.info("authserver config completed")
      if self.service_bgscheduler in self.services:
        Log.info("bgscheduler config started")
        self.process_bgscheduler()
        Log.info("bgscheduler config completed")
      if self.service_bgworker in self.services:
        Log.info("bgworker config started")
        self.process_bgworker()
        Log.info("bgworker config completed")

    except Exception as e:
      raise S3PROVError(f'process() failed with exception: {e}')

  def process_common(self):
    """ Prcoess mini provsioner steps common to all the services."""
    Log.info("copy cluster config files started")
    self.copy_config_files([self.get_confkey('S3_CLUSTER_CONFIG_FILE'),
                    self.get_confkey('S3_CLUSTER_CONFIG_SAMPLE_FILE'),
                    self.get_confkey('S3_CLUSTER_CONFIG_UNSAFE_ATTR_FILE')])
    Log.info("copy cluster config files completed")

    Log.info("cluster config update started")
    self.update_s3_cluster_configs()
    Log.info("cluster config update completed")

    # validating cluster config file after copying and updating to /etc/cortx
    Log.info("validate s3 cluster config file started")
    self.validate_config_file(self.get_confkey('S3_CLUSTER_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              self.get_confkey('S3_CLUSTER_CONFIG_SAMPLE_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              'yaml://')
    Log.info("validate s3 cluster config file completed")

    Log.info("Backing up s3 cluster sample file to temp dir started")
    self.make_sample_old_files([self.get_confkey('S3_CLUSTER_CONFIG_SAMPLE_FILE')])
    Log.info("Backing up s3 cluster sample file to temp dir complete")

  def process_s3server(self):
    """ Process mini provisioner for s3server."""
    # copy config files from /opt/seagate to base dir of config files (/etc/cortx)
    Log.info("copy s3 config files started")
    self.copy_config_files([self.get_confkey('S3_CONFIG_FILE'),
                    self.get_confkey('S3_CONFIG_SAMPLE_FILE'),
                    self.get_confkey('S3_CONFIG_UNSAFE_ATTR_FILE')])
    Log.info("copy s3 config files completed")

    # update s3 config file
    self.update_s3_server_configs()

    # validating s3 config file after copying and updating to /etc/cortx
    Log.info("validate s3 config file started")
    self.validate_config_file(self.get_confkey('S3_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                                self.get_confkey('S3_CONFIG_SAMPLE_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                                'yaml://')
    Log.info("validate s3 config files completed")

    Log.info("create symbolic link of FID config files started")
    self.create_symbolic_link_fid()
    Log.info("create symbolic link of FID config files started")

    Log.info("Backing up s3 config sample file to temp dir started")
    self.make_sample_old_files([self.get_confkey('S3_CONFIG_SAMPLE_FILE')])
    Log.info("Backing up s3 config sample file to temp dir complete")

  def process_haproxy(self):
    """ Process mini provisioner for haproxy."""
    # configure haproxy
    self.configure_haproxy()

  def process_authserver(self):
    """ Process mini provisioner for authserver."""
    # copy config files from /opt/seagate to base dir of config files (/etc/cortx)
    Log.info("copy authserver config files started")
    self.copy_config_files([self.get_confkey('S3_AUTHSERVER_CONFIG_FILE'),
                    self.get_confkey('S3_AUTHSERVER_CONFIG_SAMPLE_FILE'),
                    self.get_confkey('S3_AUTHSERVER_CONFIG_UNSAFE_ATTR_FILE'),
                    self.get_confkey('S3_KEYSTORE_CONFIG_FILE'),
                    self.get_confkey('S3_KEYSTORE_CONFIG_SAMPLE_FILE'),
                    self.get_confkey('S3_KEYSTORE_CONFIG_UNSAFE_ATTR_FILE')])
    Log.info("copy authserver config files completed")

    # copy s3 authserver resources to base dir of config files (/etc/cortx)
    Log.info("copy s3 authserver resources started")
    self.copy_s3authserver_resources()
    Log.info("copy s3 authserver resources completed")

    # update authserver config files
    self.update_s3_auth_configs()

    # validating auth config file after copying and updating to /etc/cortx
    Log.info("validate auth config file started")
    self.validate_config_file(self.get_confkey('S3_AUTHSERVER_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              self.get_confkey('S3_AUTHSERVER_CONFIG_SAMPLE_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              'properties://')
    self.validate_config_file(self.get_confkey('S3_KEYSTORE_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              self.get_confkey('S3_KEYSTORE_CONFIG_SAMPLE_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              'properties://')
    Log.info("validate auth config files completed")

    # read ldap credentials from config file
    Log.info("read ldap credentials started")
    self.read_ldap_credentials()
    self.read_ldap_root_credentials()
    Log.info("read ldap credentials completed")

    Log.info('create auth jks password started')
    self.create_auth_jks_password()
    Log.info('create auth jks password completed')

    # configure s3 schema
    self.push_s3_ldap_schema()

    Log.info("Backing up auth server config sample file to temp dir started")
    self.make_sample_old_files([self.get_confkey('S3_AUTHSERVER_CONFIG_SAMPLE_FILE'),
                                self.get_confkey('S3_KEYSTORE_CONFIG_SAMPLE_FILE')])
    Log.info("Backing up auth server config sample file to temp dir complete")

  def process_bgscheduler(self):
    """ Process mini provisioner for bgscheduler."""
    # copy config files from /opt/seagate to base dir of config files (/etc/cortx)
    Log.info("copy bgdelete config file started")
    if os.path.exists(self.get_confkey('S3_BGDELETE_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path)):
      Log.info("Skipping copy of bgdelete config file as it is already present in /etc/cortx")
    else:
      self.copy_config_files([self.get_confkey('S3_BGDELETE_CONFIG_FILE'),
                      self.get_confkey('S3_BGDELETE_CONFIG_SAMPLE_FILE'),
                      self.get_confkey('S3_BGDELETE_CONFIG_UNSAFE_ATTR_FILE')])
    Log.info("copy bgdelete config file completed")

    # update s3 bgdelete scheduler config
    self.update_s3_bgdelete_scheduler_configs()

    # validating s3 bgdelete scheduler config file after copying and updating to /etc/cortx
    Log.info("validate s3 bgdelete scheduler config file started")
    self.validate_config_file(self.get_confkey('S3_BGDELETE_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              self.get_confkey('S3_BGDELETE_CONFIG_SAMPLE_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              'yaml://')
    Log.info("validate s3 bgdelete scheduler config files completed")

    # create topic for background delete
    bgdeleteconfig = CORTXS3Config(self.base_config_file_path, "yaml://")
    if bgdeleteconfig.get_messaging_platform() == MESSAGE_BUS:
      Log.info('Create topic started')
      self.create_topic(bgdeleteconfig.get_msgbus_admin_id,
                        bgdeleteconfig.get_msgbus_topic(),
                        self.get_msgbus_partition_count())
      Log.info('Create topic completed')

    Log.info("Backing up s3 bgdelete config sample file to temp dir started")
    self.make_sample_old_files([self.get_confkey('S3_BGDELETE_CONFIG_SAMPLE_FILE')])
    Log.info("Backing up s3 bgdelete config sample file to temp dir complete")

  def process_bgworker(self):
    """ Process mini provisioner for bgworker."""
    # copy config files from /opt/seagate to base dir of config files (/etc/cortx)
    Log.info("copy bgdelete config file started")
    if os.path.exists(self.get_confkey('S3_BGDELETE_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path)):
      Log.info("Skipping copy of bgdelete config file as it is already present in /etc/cortx")
    else:
      self.copy_config_files([self.get_confkey('S3_BGDELETE_CONFIG_FILE'),
                      self.get_confkey('S3_BGDELETE_CONFIG_SAMPLE_FILE'),
                      self.get_confkey('S3_BGDELETE_CONFIG_UNSAFE_ATTR_FILE')])
    Log.info("copy bgdelete config file completed")

    # update s3 bgdelete scheduler config as its a floating pod.
    # it should has access to updated scheduler config on every node
    # Note: update_s3_bgdelete_scheduler_configs() can be removed once we move to consul
    self.update_s3_bgdelete_scheduler_configs()
    # update s3 bgdelete worker config
    self.update_s3_bgdelete_worker_configs()

    # validating s3 bgdelete worker config file after copying and updating to /etc/cortx
    Log.info("validate s3 bgdelete worker config file started")
    self.validate_config_file(self.get_confkey('S3_BGDELETE_CONFIG_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              self.get_confkey('S3_BGDELETE_CONFIG_SAMPLE_FILE').replace("/opt/seagate/cortx", self.base_config_file_path),
                              'yaml://')
    Log.info("validate s3 bgdelete worker config files completed")

    # read ldap credentials from config file
    Log.info("read ldap credentials started")
    self.read_ldap_credentials()
    self.read_ldap_root_credentials()
    Log.info("read ldap credentials completed")

    # create topic for background delete
    bgdeleteconfig = CORTXS3Config(self.base_config_file_path, "yaml://")
    if bgdeleteconfig.get_messaging_platform() == MESSAGE_BUS:
      Log.info('Create topic started')
      self.create_topic(bgdeleteconfig.get_msgbus_admin_id,
                        bgdeleteconfig.get_msgbus_topic(),
                        self.get_msgbus_partition_count())
      Log.info('Create topic completed')

    # create background delete account
    ldap_endpoint_fqdn = self.get_endpoint("CONFIG>CONFSTORE_S3_OPENLDAP_ENDPOINTS", "fqdn", "ldap")

    Log.info("create background delete account started")
    self.create_bgdelete_account(ldap_endpoint_fqdn)
    Log.info("create background delete account completed")

    Log.info("Backing up s3 bgdelete config sample file to temp dir started")
    self.make_sample_old_files([self.get_confkey('S3_BGDELETE_CONFIG_SAMPLE_FILE')])
    Log.info("Backing up s3 bgdelete config sample file to temp dir complete")

  def create_symbolic_link(self, src_path: str, dst_path: str):
    """create symbolic link."""
    Log.info(f"symbolic link source path: {src_path}")
    Log.info(f"symbolic link destination path: {dst_path}")
    if os.path.exists(dst_path):
      Log.info(f"symbolic link is already present")
      os.unlink(dst_path)
      Log.info("symbolic link is unlinked")
    os.symlink(src_path, dst_path)
    Log.info(f"symbolic link created successfully")

  def create_symbolic_link_fid(self):
    """ Create symbolic link of FID sysconfig file."""
    sysconfig_path = os.path.join(self.base_config_file_path,"s3","sysconfig",self.machine_id)
    file_name = sysconfig_path + '/s3server-0x*'
    list_matching = []
    for name in glob.glob(file_name):
      list_matching.append(name)
    count = len(list_matching)
    Log.info(f"s3server FID file count : {count}")
    s3_instance_count = int(self.get_confvalue_with_defaults('CONFIG>CONFSTORE_S3INSTANCES_KEY'))
    Log.info(f"s3_instance_count : {s3_instance_count}")
    if count < s3_instance_count:
      raise Exception("HARE-sysconfig file count does not match s3 instance count")
    index = 1
    for src_path in list_matching:
      file_name = 's3server-' + str(index)
      dst_path = os.path.join(sysconfig_path, file_name)
      self.create_symbolic_link(src_path, dst_path)
      index += 1

  def push_s3_ldap_schema(self):
      """ Push s3 ldap schema with below checks,
          1. While pushing schema, global lock created in consul kv store as <index, key, value>.
             e.g. <s3_consul_index, component>s3>openldap_lock, machine_id>
          2. Before pushing s3 schema,
             a. Check for s3 openldap lock from consul kv store
             b. if lock is None/machine-id, then go ahead and push s3 ldap schema.
             c. if lock has other values except machine-id/None, then wait for the lock and retry again.
          3. Once s3 schema is pushed, delete the s3 key from consul kv store.
      """
      ldap_lock = False
      Log.info('checking for concurrent execution scenario for s3 ldap scheam push using consul kv lock.')
      openldap_key=self.get_confkey("S3_CONSUL_OPENLDAP_KEY")

      while(True):
          try:
              opendldap_val = self.consul_confstore.get_config(f'{openldap_key}')
              Log.info(f'openldap lock value is:{opendldap_val}')
              if opendldap_val is None:
                  Log.info(f'Setting confstore value for key :{openldap_key} and value as :{self.machine_id}')
                  self.consul_confstore.set_config(f'{openldap_key}', f'{self.machine_id}', True)
                  Log.info('Updated confstore with latest value')
                  time.sleep(3)
                  continue
              if opendldap_val == self.machine_id:
                  Log.info(f'Found lock acquired successfully hence processing with openldap schema push')
                  ldap_lock = True
                  break
              if opendldap_val != self.machine_id:
                  Log.info(f'openldap lock is already acquired by {opendldap_val}, Hence skipping openldap schema configuration')
                  # this is necessary - this makes sure that openldap schema is pushed before proceeding further (for account creation)
                  # else it happens that account creation is attempted before schema was pushed on given openldap server
                  time.sleep(3)
                  continue

          except Exception as e:
              Log.error(f'Exception occured while connecting consul service endpoint {e}')
              break
      if ldap_lock == True:
        # push openldap schema
        Log.info('Pushing s3 ldap schema ....!!')
        self.configure_s3_schema()
        Log.info('Pushed s3 ldap schema successfully....!!')
        Log.info(f'Deleting consule key :{openldap_key}')
        self.consul_confstore.delete_key(f'{openldap_key}', True)
        Log.info(f'deleted openldap key-value from consul')

  def configure_s3_schema(self):
    Log.info('openldap s3 configuration started')
    server_nodes_list_key = self.get_confkey('CONFIG>CONFSTORE_S3_OPENLDAP_SERVERS')
    server_nodes_list = self.get_confvalue(server_nodes_list_key)
    if type(server_nodes_list) is str:
      # list is stored as string in the confstore file
      server_nodes_list = literal_eval(server_nodes_list)
    for node_machine_id in server_nodes_list:
        cmd = ['/opt/seagate/cortx/s3/install/ldap/s3_setup_ldap.sh',
                '--hostname',
                f'{node_machine_id}',
                '--ldapuser',
                f'{self.ldap_user}',
                '--ldapadminpasswd',
                f'{self.ldap_passwd}',
                '--rootdnpasswd',
                f'{self.rootdn_passwd}']
        handler = SimpleProcess(cmd)
        stdout, stderr, retcode = handler.run()
        Log.info(f'output of setup_ldap.sh: {stdout}')
        if retcode != 0:
          Log.error(f'error of setup_ldap.sh: {stderr} {node_machine_id}')
          raise S3PROVError(f"{cmd} failed with err: {stderr}, out: {stdout}, ret: {retcode}")
        else:
          Log.warn(f'warning of setup_ldap.sh: {stderr} {node_machine_id}')

  def create_topic(self, admin_id: str, topic_name:str, partitions: int):
    """create topic for background delete services."""
    try:
      if not S3CortxMsgBus.is_topic_exist(admin_id, topic_name):
          S3CortxMsgBus.create_topic(admin_id, [topic_name], partitions)
          Log.info("Topic Created")
      else:
          Log.info("Topic Already exists")
    except Exception as e:
      raise e

  def get_msgbus_partition_count_Ex(self):
    """get total server nodes which will act as partition count."""
    # Get storage set count to loop over to get all nodes
    storage_set_count = self.get_confvalue_with_defaults('CONFIG>CONFSTORE_STORAGE_SET_COUNT')
    Log.info(f"storage_set_count : {storage_set_count}")
    srv_io_node_count = 0
    index = 0
    while index < int(storage_set_count):
      # Get all server nodes
      server_nodes_list_key = self.get_confkey('CONFIG>CONFSTORE_STORAGE_SET_SERVER_NODES_KEY').replace("storage_set_count", str(index))
      Log.info(f"server_nodes_list_key : {server_nodes_list_key}")
      server_nodes_list = self.get_confvalue(server_nodes_list_key)
      for server_node_id in server_nodes_list:
        Log.info(f"server_node_id : {server_node_id}")
        server_node_type_key = self.get_confkey('CONFIG>CONFSTORE_NODE_TYPE').replace('node-id', server_node_id)
        Log.info(f"server_node_type_key : {server_node_type_key}")
        # Get the type of each server node
        server_node_type = self.get_confvalue(server_node_type_key)
        Log.info(f"server_node_type : {server_node_type}")
        if server_node_type == "storage_node":
          Log.info(f"Node type is storage_node")
          srv_io_node_count += 1
      index += 1

    Log.info(f"Server io node count : {srv_io_node_count}")

    # Partition count should be ( number of hosts * 2 )
    partition_count = srv_io_node_count * 2
    Log.info(f"Partition count : {partition_count}")
    return partition_count

  def get_msgbus_partition_count(self):
    """get total consumers (* 2) which will act as partition count."""
    consumer_count = 0
    search_values = self.search_confvalue("node", "services", self.bg_delete_service)
    consumer_count = len(search_values)
    Log.info(f"consumer_count : {consumer_count}")

    # Partition count should be ( number of consumer * 2 )
    partition_count = consumer_count * 2
    Log.info(f"Partition count : {partition_count}")
    return partition_count

  def configure_haproxy(self):
    """Configure haproxy service."""
    Log.info('haproxy configuration started')
    try:
      # Create main config file for haproxy.
      S3HaproxyConfig(self.url).process()
      Log.info("Successfully configured haproxy on the node.")
    except Exception as e:
      Log.error(f'Failed to configure haproxy for s3server, error: {e}')
      raise e
    Log.info('haproxy configuration completed')

  def create_auth_jks_password(self):
    """Create random password for auth jks keystore."""
    cmd = ['sh',
      '/opt/seagate/cortx/auth/scripts/create_auth_jks_password.sh', self.base_config_file_path]
    handler = SimpleProcess(cmd)
    stdout, stderr, retcode = handler.run()
    Log.info(f'output of create_auth_jks_password.sh: {stdout}')
    if retcode != 0:
      Log.error(f'error of create_auth_jks_password.sh: {stderr}')
      raise S3PROVError(f"{cmd} failed with err: {stderr}, out: {stdout}, ret: {retcode}")
    else:
      Log.warn(f'warning of create_auth_jks_password.sh: {stderr}')
      Log.info(' Successfully set auth JKS keystore password.')

  def create_bgdelete_account(self, ldap_endpoint_fqdn: str):
    """ create bgdelete account."""
    try:
      # Create background delete account
      bgdelete_acc_input_params_dict = self.get_config_param_for_BG_delete_account()
      ldap_host_url="ldap://"+ldap_endpoint_fqdn
      LdapAccountAction(self.ldap_user, self.ldap_passwd).create_account(bgdelete_acc_input_params_dict, ldap_host_url)
    except Exception as e:
      if "Already exists" not in str(e):
        Log.error(f'Failed to create backgrounddelete service account, error: {e}')
        raise(e)
      else:
        Log.warn("backgrounddelete service account already exist")

  def update_config_value(self, config_file_path : str,
                          config_file_type : str,
                          key_to_read : str,
                          key_to_update: str,
                          modifier_function = None,
                          additional_param = None):
    """Update provided config key and value to provided config file.
       Modifier function should have the signature func_name(confstore, value)."""

    # validate config file exist (example: configfile = /etc/cortx/s3/conf/s3config.yaml)
    configfile = self.get_confkey(config_file_path).replace("/opt/seagate/cortx", self.base_config_file_path)
    if path.isfile(f'{configfile}') == False:
      Log.error(f'{configfile} file is not present')
      raise S3PROVError(f'{configfile} file is not present')

    # load config file (example: s3configfileconfstore = confstore object to /etc/cortx/s3/conf/s3config.yaml)
    s3configfileconfstore = S3CortxConfStore(f'{config_file_type}://{configfile}', 'update_config_file_idx' + key_to_update)

    # get the value to be updated from provisioner config for given key
    # Fetchinng the incoming value from the provisioner config file
    # Which should be updated to key_to_update in s3 config file
    value_to_update = self.get_confvalue_with_defaults(key_to_read)

    if modifier_function is not None:
      Log.info(f'Modifier function name : {modifier_function.__name__}')
      value_to_update = modifier_function(value_to_update, additional_param)

    Log.info(f'Key to update: {key_to_read}')
    Log.info(f'Value to update: {value_to_update}')

    # set the config value in to config file (example: s3 config file key_to_update = value_to_update, and save)
    s3configfileconfstore.set_config(key_to_update, value_to_update, True)
    Log.info(f'Key {key_to_update} updated successfully in {configfile}')

  def update_s3_server_configs(self):
    """ Update s3 server configs."""
    Log.info("Update s3 server config file started")
    #self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3SERVER_PORT", "S3_SERVER_CONFIG>S3_SERVER_BIND_PORT")
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_BGDEL_BIND_ADDR", "S3_SERVER_CONFIG>S3_SERVER_BGDELETE_BIND_ADDR")
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_INTERNAL_ENDPOINTS", "S3_SERVER_CONFIG>S3_SERVER_BGDELETE_BIND_PORT",self.update_s3_bgdelete_bind_port)
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_AUTHSERVER_IP_ADDRESS", "S3_AUTH_CONFIG>S3_AUTH_IP_ADDR")
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_AUTHSERVER_PORT", "S3_AUTH_CONFIG>S3_AUTH_PORT")
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_ENABLE_STATS", "S3_SERVER_CONFIG>S3_ENABLE_STATS")
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_AUDIT_LOGGER", "S3_SERVER_CONFIG>S3_AUDIT_LOGGER_POLICY")
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_BASE_LOG_PATH", "S3_SERVER_CONFIG>S3_LOG_DIR", self.update_s3_log_dir_path)
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_BASE_LOG_PATH", "S3_SERVER_CONFIG>S3_DAEMON_WORKING_DIR", self.update_s3_daemon_working_dir)
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_MOTR_MAX_UNITS_PER_REQUEST", "S3_MOTR_CONFIG>S3_MOTR_MAX_UNITS_PER_REQUEST", self.update_motr_max_unit_per_request)
    self.update_config_value("S3_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_MOTR_MAX_START_TIMEOUT", "S3_MOTR_CONFIG>S3_MOTR_INIT_MAX_TIMEOUT")
    Log.info("Update s3 server config file completed")

  def update_s3_bgdelete_bind_port(self, value_to_update, additional_param):
    if isinstance(value_to_update, str):
      value_to_update = literal_eval(value_to_update)
    endpoint = self.get_endpoint_for_scheme(value_to_update, "http")
    if 'port' not in endpoint:
      raise S3PROVError(f"BG Delete endpoint {value_to_update} does not have port specified.")
    if ("K8" == str(self.get_confvalue_with_defaults('CONFIG>CONFSTORE_SETUP_TYPE'))) :
      return int(endpoint['port']) -1
    else :
      return int(endpoint['port'])

  def update_s3_log_dir_path(self, value_to_update, additional_param):
    """ Update s3 server log directory path."""
    s3_log_dir_path = os.path.join(value_to_update, "s3", self.machine_id)
    Log.info(f"s3_log_dir_path : {s3_log_dir_path}")
    return s3_log_dir_path

  def update_s3_daemon_working_dir(self, value_to_update, additional_param):
    """ Update s3 daemon working log directory."""
    s3_daemon_working_dir = os.path.join(value_to_update, "motr", self.machine_id)
    Log.info(f"s3_daemon_working_dir : {s3_daemon_working_dir}")
    return s3_daemon_working_dir

  def update_motr_max_unit_per_request(self, value_to_update, additional_param):
    """Update motr max unit per request."""
    if 2 <= int(value_to_update) <= 128:
      if math.log2(int(value_to_update)).is_integer():
        Log.info("motr_max_units_per_request is in valid range")
      else:
        raise S3PROVError("motr_max_units_per_request should be power of 2")
    else:
      raise S3PROVError("motr_max_units_per_request should be between 2 to 128")
    return int(value_to_update)

  def update_s3_auth_configs(self):
    """ Update s3 auth configs."""
    Log.info("Update s3 authserver config file started")
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_AUTHSERVER_HTTP_PORT", "httpPort")
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_AUTHSERVER_HTTPS_PORT", "httpsPort")
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_OPENLDAP_ENDPOINTS", "ldapHost",self.update_auth_ldap_host)
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_OPENLDAP_ENDPOINTS", "ldapPort",self.update_auth_ldap_nonssl_port)
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_OPENLDAP_ENDPOINTS", "ldapSSLPort",self.update_auth_ldap_ssl_port)
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_AUTHSERVER_DEFAULT_ENDPOINT", "defaultEndpoint")
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_S3_AUTHSERVER_IAM_AUDITLOG", "IAMAuditlog")
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_BASE_LOG_PATH", "logFilePath", self.update_auth_log_dir_path)
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_BASE_CONFIG_PATH", "logConfigFile", self.update_auth_log4j_config_file_path)
    self.update_auth_log4j_log_dir_path()
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_LDAPADMIN_USER_KEY", "ldapLoginDN", self.update_auth_ldap_login_dn)
    self.update_config_value("S3_AUTHSERVER_CONFIG_FILE", "properties", "CONFIG>CONFSTORE_LDAPADMIN_PASSWD_KEY", "ldapLoginPW")
    Log.info("Update s3 authserver config file completed")

  def update_auth_ldap_host (self, value_to_update, additional_param):
    if type(value_to_update) is str:
      value_to_update = literal_eval(value_to_update)
    endpoint = self.get_endpoint_for_scheme(value_to_update, "ldap")
    if endpoint is None:
      raise S3PROVError(f"OpenLDAP endpoint for scheme 'ldap' is not specified")
    return endpoint['fqdn']


  def update_auth_ldap_ssl_port(self, value_to_update, additional_param):
    if type(value_to_update) is str:
      value_to_update = literal_eval(value_to_update)
    endpoint = self.get_endpoint_for_scheme(value_to_update, "ssl")
    if endpoint is None:
      raise S3PROVError(f"SSL LDAP endpoint is not specified.")
    if 'port' not in endpoint:
      raise S3PROVError(f"SSL LDAP endpoint does not specify port number.")
    return endpoint['port']

  def update_auth_ldap_nonssl_port(self, value_to_update, additional_param):
    if type(value_to_update) is str:
      value_to_update = literal_eval(value_to_update)
    endpoint = self.get_endpoint_for_scheme(value_to_update, "ldap")
    if endpoint is None:
      raise S3PROVError(f"Non-SSL LDAP endpoint is not specified.")
    if 'port' not in endpoint:
      raise S3PROVError(f"Non-SSL LDAP endpoint does not specify port number.")
    return endpoint['port']

  def update_auth_log_dir_path(self, value_to_update, additional_param):
    """Update s3 auth log directory path in config file."""
    s3_auth_log_path = os.path.join(value_to_update, "auth", self.machine_id, "server")
    Log.info(f's3_auth_log_path: {s3_auth_log_path}')
    return s3_auth_log_path

  def update_auth_log4j_config_file_path(self, value_to_update, additional_param):
    """Update s3 auth log4j config path in config file."""
    s3_auth_log4j_log_path = self.get_confkey("S3_AUTHSERVER_LOG4J2_CONFIG_FILE").replace("/opt/seagate/cortx", self.base_config_file_path)
    Log.info(f's3_auth_log4j_log_path: {s3_auth_log4j_log_path}')
    return s3_auth_log4j_log_path

  def update_auth_log4j_log_dir_path(self):
    """Update s3 auth log directory path in log4j2 config file."""
    # validate config file exist
    log4j2_configfile = self.get_confkey("S3_AUTHSERVER_LOG4J2_CONFIG_FILE").replace("/opt/seagate/cortx", self.base_config_file_path)
    if path.isfile(f'{log4j2_configfile}') == False:
      Log.error(f'{log4j2_configfile} file is not present')
      raise S3PROVError(f'{log4j2_configfile} file is not present')
    # parse the log4j xml file
    log4j2_xmlTree = ET.parse(log4j2_configfile)
    # get the root node
    rootElement = log4j2_xmlTree.getroot()
    # find the node Properties/Property
    propertiesElement = rootElement.find("Properties")
    propertyElement = propertiesElement.find("Property")
    s3_auth_log_path = os.path.join(self.base_log_file_path, "auth", self.machine_id, "server")
    Log.info(f's3_auth_log_path: {s3_auth_log_path}')
    # update the path in to xml
    propertyElement.text = s3_auth_log_path
    # Write the modified xml file.
    log4j2_xmlTree.write(log4j2_configfile)
    Log.info(f'Updated s3 auth log directory path in log4j2 config file')
  
  def update_auth_ldap_login_dn(self, value_to_update, additional_param):
    """Update s3 auth ldap login DN in config file."""
    s3_auth_ldap_login_dn = "cn=" + str(value_to_update) + ",dc=seagate,dc=com"
    Log.info(f's3_auth_ldap_login_dn: {s3_auth_ldap_login_dn}')
    return s3_auth_ldap_login_dn

  def update_s3_bgdelete_scheduler_configs(self):
    """ Update s3 bgdelete scheduler configs."""
    Log.info("Update s3 bgdelete scheduler config file started")
    self.update_config_value("S3_BGDELETE_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_INTERNAL_ENDPOINTS", "cortx_s3>producer_endpoint",self.update_bgdelete_producer_endpoint)
    self.update_config_value("S3_BGDELETE_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_BGDELETE_SCHEDULER_SCHEDULE_INTERVAL", "cortx_s3>scheduler_schedule_interval")
    self.update_config_value("S3_BGDELETE_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_BGDELETE_MAX_KEYS", "indexid>max_keys")
    self.update_config_value("S3_BGDELETE_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_BASE_LOG_PATH", "logconfig>scheduler_logger_directory", self.update_bgdelete_scheduler_log_dir)
    Log.info("Update s3 bgdelete scheduler config file completed")

  def update_s3_bgdelete_worker_configs(self):
    """ Update s3 bgdelete worker configs."""
    Log.info("Update s3 bgdelete worker config file started")
    self.update_config_value("S3_BGDELETE_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_S3_BGDELETE_CONSUMER_ENDPOINT", "cortx_s3>consumer_endpoint", self.update_bgdelete_consumer_endpoint)
    self.update_config_value("S3_BGDELETE_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_BASE_LOG_PATH", "logconfig>processor_logger_directory", self.update_bgdelete_processor_log_dir)
    Log.info("Update s3 bgdelete worker config file completed")

  def update_bgdelete_producer_endpoint(self, value_to_update, additional_param):
    if isinstance(value_to_update, str):
      value_to_update = literal_eval(value_to_update)
    endpoint = self.get_endpoint_for_scheme(value_to_update, "http")
    if endpoint is None:
      raise S3PROVError(f"BG Producer endpoint for scheme 'http' is not specified")
    return endpoint['scheme'] + "://" + endpoint['fqdn'] + ":" + endpoint['port']

  def update_bgdelete_consumer_endpoint(self, value_to_update, additional_param):
    if isinstance(value_to_update, str):
      value_to_update = literal_eval(value_to_update)
    endpoint = self.get_endpoint_for_scheme(value_to_update, "http")
    if endpoint is None:
      raise S3PROVError(f"BG Consumer endpoint for scheme 'http' is not specified")
    if ("K8" == str(self.get_confvalue_with_defaults('CONFIG>CONFSTORE_SETUP_TYPE'))) :
      endpoint['port'] = int(endpoint['port']) -1
    else :
      endpoint['port'] = int(endpoint['port'])
    return endpoint['scheme'] + "://" + endpoint['fqdn'] + ":" + str(endpoint['port'])

  # In producer we do not append machine ID to path but below two functtions are for future 
  def update_bgdelete_scheduler_log_dir(self, value_to_update, additional_param):
    """ Update s3 bgdelete Scheduler log dir path."""
    bgdelete_log_dir_path = os.path.join(value_to_update, "s3", "s3backgrounddelete")
    Log.info(f"update_bgdelete_scheduler_log_dir : {bgdelete_log_dir_path}")
    return bgdelete_log_dir_path

  def update_bgdelete_processor_log_dir(self, value_to_update, additional_param):
    """ Update s3 bgdelete processor log dir path."""
    bgdelete_log_dir_path = os.path.join(value_to_update, "s3", self.machine_id, "s3backgrounddelete")
    Log.info(f"update_bgdelete_processor_log_dir : {bgdelete_log_dir_path}")
    return bgdelete_log_dir_path

  def update_s3_cluster_configs(self):
    """ Update s3 cluster configs."""
    Log.info("Update s3 cluster config file started")
    self.update_config_value("S3_CLUSTER_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_CLUSTER_ID_KEY", "cluster_config>cluster_id")
    self.update_config_value("S3_CLUSTER_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_ROOTDN_USER_KEY", "cluster_config>rootdn_user")
    self.update_config_value("S3_CLUSTER_CONFIG_FILE", "yaml", "CONFIG>CONFSTORE_ROOTDN_PASSWD_KEY", "cluster_config>rootdn_pass")
    Log.info("Update s3 cluster config file completed")

  def copy_s3authserver_resources(self):
    """Copy config files from /opt/seagate/cortx/auth/resources  to /etc/cortx/auth/resources."""
    src_authserver_resource_dir= self.get_confkey("S3_AUTHSERVER_RESOURCES_DIR")
    dest_authserver_resource_dir= self.get_confkey("S3_AUTHSERVER_RESOURCES_DIR").replace("/opt/seagate/cortx", self.base_config_file_path)
    for item in os.listdir(src_authserver_resource_dir):
      source = os.path.join(src_authserver_resource_dir, item)
      destination = os.path.join(dest_authserver_resource_dir, item)
      if os.path.isdir(source):
        if os.path.exists(destination):
          shutil.rmtree(destination)
        shutil.copytree(source, destination)
      else:
          shutil.copy2(source, destination)

  def copy_logrotate_files(self):
    """Copy log rotation config files from install directory to cron directory."""
    # Copy log rotate config files to /etc/logrotate.d/
    config_files = [self.get_confkey('S3_LOGROTATE_AUDITLOG')]
    self.copy_logrotate_files_crond(config_files, "/etc/logrotate.d/")

    # Copy log rotate config files to /etc/cron.hourly/
    config_files = [self.get_confkey('S3_LOGROTATE_S3LOG'),
                    self.get_confkey('S3_LOGROTATE_M0TRACE'),
                    self.get_confkey('S3_LOGROTATE_ADDB')]
    self.copy_logrotate_files_crond(config_files, "/etc/cron.hourly/")

  def copy_logrotate_files_crond(self, config_files, dest_directory):
    """Copy log rotation config files from install directory to cron directory."""
    # Copy log rotation config files from install directory to cron directory
    for config_file in config_files:
      Log.info(f"Source config file: {config_file}")
      Log.info(f"Dest dir: {dest_directory}")
      os.makedirs(os.path.dirname(dest_directory), exist_ok=True)
      shutil.copy(config_file, dest_directory)
      Log.info("Config file copied successfully to cron directory")

  def find_and_replace(self, filename: str,
                       content_to_search: str,
                       content_to_replace: str):
    """find and replace the given string."""
    Log.info(f"content_to_search: {content_to_search}")
    Log.info(f"content_to_replace: {content_to_replace}")
    with open(filename) as f:
      newText=f.read().replace(content_to_search, content_to_replace)
    with open(filename, "w") as f:
      f.write(newText)
    Log.info(f"find and replace completed successfully for the file {filename}.")
