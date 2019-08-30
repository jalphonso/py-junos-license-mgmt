import argparse
import ruamel.yaml
import sys
from ansible.parsing.dataloader import DataLoader
from ansible.inventory.manager import InventoryManager
from ansible.vars.manager import VariableManager
from colorama import Fore, Style
from validate import validate_bool, validate_password, validate_choice, validate_str, validate_int
from lxml import etree
from jnpr.junos import Device
from jnpr.junos.exception import ConnectError, ProbeError, ConnectAuthError
from jnpr.junos.utils.scp import SCP
from os import listdir
from os.path import isfile, join
from pathlib import Path

yaml = ruamel.yaml.YAML()
yaml.indent(sequence=4, offset=2)
yaml.explicit_start = True


def main():
  parser = argparse.ArgumentParser(
      description='Execute troubleshooting operation(s)')
  parser.add_argument('-u', '--user', dest='user', metavar='<username>',
                      help='provide username for ssh login to devices')
  parser.add_argument('-p', '--pass', dest='passwd', metavar='<password>',
                      help='provide ssh password or passphrase')
  parser.add_argument('-n', '--nopass', action='store_true',
                      help='disable password prompting')
  parser.add_argument('-c', '--config', dest='ssh_config', metavar='<ssh_config>', default='',
                      help='provide ssh config path')
  parser.add_argument('-i', '--inventory', dest='inventory_path', metavar='<inventory_path>',
                      help='provide ansible inventory path')
  parser.add_argument('-l', '--limit', dest='limit', metavar='<limit>',
                      help='specify host or group to run operations on')
  parser.add_argument('-q', '--quiet', action='store_true',
                      help='disable optional interactive prompts')

  args = parser.parse_args()

  print(f"{Fore.YELLOW}Welcome to the Python license management script for Junos boxes using PyEZ{Style.RESET_ALL}")
  if (not args.user and not args.inventory_path and not args.quiet and
      validate_bool("Would you like to print the command line help? (y/n) "
                    "(type n to continue in interactive mode) ")):
    parser.print_help()
    sys.exit(0)

  user = validate_str("Enter your username: ", cli_input=args.user)
  if args.nopass:
    passwd = None
  else:
    passwd = validate_password("Enter your password: ", cli_input=args.passwd)
  if args.ssh_config:
    ssh_config = validate_str(
        "Enter path to ssh config: ", cli_input=args.ssh_config)
  else:
    ssh_config = None

  if not args.inventory_path:
    inventory_dir = Path("inventory")
    inventory_choices = [x for x in inventory_dir.iterdir() if x.is_dir()]
    inventory_choices.sort()
    print("\nAvailable Datacenters:")
    for idx, choice in enumerate(inventory_choices):
      print(f"{idx+1}: {choice.name}")
    user_choice = validate_int("\nSelect Datacenter (Type Number only and press Enter):", input_min=1,
                               input_max=inventory_choices.__len__())
    choice = inventory_choices[user_choice - 1]
    datacenter = choice.as_posix()
    print(f"Datacenter {choice.name} selected")
  else:
    datacenter = args.inventory_path
  # Ensure inventory path exists. Safeguard mainly when user provides path via cmd line
  if not Path(datacenter).exists():
    print(f"Inventory Path '{datacenter}' does not exist. quitting...")
    sys.exit(1)

  if (not args.limit and not args.quiet and
          validate_bool("Do you want to limit the execution to a specific set of hosts or groups? (y/n) ")):
    limit = validate_str("Wildcard matching is supported like * and ? or [1-6] or [a:d] "
                         "i.e. qfx5?00-[a:d] or qfx5100*\nEnter your limit: ")
  elif args.limit:
    limit = args.limit
  else:
    limit = None

  loader = DataLoader()
  inventory = InventoryManager(loader=loader, sources=datacenter)
  variables = VariableManager(loader=loader, inventory=inventory)
  for host in inventory.get_hosts():
    hostname = host.get_name()
    netconf_port = variables.get_vars(host=host)['netconf_port']
    ansible_host = variables.get_vars(host=host)['ansible_host']

    # Begin Device Output to User
    print(f"{Fore.BLUE}{Style.BRIGHT}Checking existing host_vars structure for device {hostname}{Style.RESET_ALL}")
    try:
      host_path = Path(f'{datacenter}/host_vars/{hostname}')
      license_path = Path(f'{datacenter}/host_vars/{hostname}/licenses.yml')
      if not host_path.exists():
        print(f"Creating host_vars directory for host {hostname}")
        host_path.mkdir()
        old_yml_path = Path(f'{datacenter}/host_vars/{hostname}.yml')
        new_yml_path = Path(f'{datacenter}/host_vars/{hostname}/system.yml')
        if old_yml_path.exists():
          print(f"Moving old host_vars file for {hostname} to {new_yml_path}")

          old_yml_path.rename(new_yml_path)

      if license_path.exists():
        print(f"loading license.yml for host {hostname}")
        licenses = yaml.load(license_path)
      else:
        print(f"initializing license.yml for host {hostname}")
        licenses = {}

      if 'license_keys' not in licenses:
        licenses['license_keys'] = []

      # Saving a copy for comparison later to see if we need to write updates to the yml
      original_licenses_in_yml = licenses['license_keys'].copy()

      print(f"{Fore.BLUE}{Style.BRIGHT}Retrieving existing licenses for device {hostname}{Style.RESET_ALL}")
      with Device(host=ansible_host, port=netconf_port, user=user, passwd=passwd, ssh_config=ssh_config,
                  auto_probe=5) as dev:
        serial = dev.facts['serialnumber']
        hostname = dev.facts['hostname']
        print(f"device {hostname} has serialnumber {serial}")
        license_keys = dev.rpc.get_license_key_information()
        for key in license_keys:
          key_data = key.xpath('key-data')
          key_data = key_data[0].text.strip()
          key_data = ' '.join(key_data.split('\n'))
          key_data = ' '.join(key_data.split())
          if key_data not in licenses['license_keys']:
            key_name = key_data.split(' ')[0]
            print(f"{Fore.MAGENTA}Found unmanaged license key {key_name} on device {hostname}. Saving...{Style.RESET_ALL}")
            licenses['license_keys'].append(key_data)

      new_license_paths = Path("licenses/").glob(f"{serial}*.txt")
      for new_license_path in new_license_paths:
        new_license = new_license_path.read_text().splitlines()[8:]
        new_license = ' '.join([line.strip()
                                for line in new_license if line is not ''])
        if new_license not in licenses['license_keys']:
          key_name = new_license.split(' ')[0]
          print(f"{Fore.MAGENTA}Found new license key {key_name} in file '{new_license_path}' for device {hostname}. Converting...{Style.RESET_ALL}")
          licenses['license_keys'].append(new_license)
      if licenses['license_keys'] != original_licenses_in_yml:
        print(f"Updating licenses.yml for host {hostname}...")
        yaml.dump(licenses, license_path)
      else:
        print(f"{Fore.GREEN}Licenses already in sync for host {hostname}{Style.RESET_ALL}")
    except ConnectAuthError as err:
      print("Unable to login. Check username/password")
      print("Exiting so you don't lock yourself out :)")
      sys.exit(1)
    except (ProbeError, ConnectError) as err:
      print("Cannot connect to device: \nMake sure device is reachable and "
            "'set system services netconf ssh' is set")
    except Exception as err:
      print(err.__class__.__name__ + ": " + err.message)
      sys.exit(1)
  print(f"{Fore.YELLOW}License Sync Complete!{Style.RESET_ALL}")


if __name__ == '__main__':
  main()
