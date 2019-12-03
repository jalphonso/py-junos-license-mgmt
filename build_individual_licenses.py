import ruamel.yaml
from colorama import Fore, Style
from pathlib import Path

yaml = ruamel.yaml.YAML()
yaml.indent(sequence=4, offset=2)
yaml.explicit_start = True


# Gather existing license ids from directory
existing_license_ids = {}
for f in Path("licenses/").iterdir():
  if 'consolidated' in f.name or '.txt' not in f.name:
    continue
  license_id = f.read_text().split(' ')[0]
  existing_license_ids[license_id] = f.name

input_file = Path('licenses/consolidated.txt')
count = 0
# keep track of qty of licenses per serial
serials = {}
with input_file.open() as f:
  while True:
    serial = f.readline()
    if serial == "\n":
      continue
    elif not serial:
      break
    serial = serial.strip()
    if serial in serials.keys():
      qty = serials[serial]
      license_file = Path(f"licenses/{serial}-{qty + 1}.txt")
      serials[serial] = serials[serial] + 1
    else:
      license_file = Path(f"licenses/{serial}.txt")
      serials[serial] = 1
    license_key = f.readline()
    license_id = license_key.split(' ')[0]

    if license_file.exists():
      # print(f"license file {license_file} already exists. skipping...")
      continue

    if license_id in existing_license_ids.keys():
      print(f"{Fore.RED}{license_id} license exists in multiple files. "
            "Check to see if one of the devices was RMA'd and remove the old file.")
      print(f"existing file: {existing_license_ids[license_id]}\nnew file: {license_file.name}{Style.RESET_ALL}")
      continue
    else:
      existing_license_ids[license_id] = license_file.name

    qty = serials[serial]
    if qty > 1:
      print(f"{Fore.YELLOW}serial {serial} has {qty} key(s). "
            "Appending a suffix of -{qty} to this one.{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Writing new license with id {license_id} to file {license_file.name}{Style.RESET_ALL}")
    license_file.touch()
    license_file.chmod(0o660)
    license_file.write_text(license_key)
    # print(serial.strip())
    # print(license_key.strip())
    count = count + 1
print(f"Processed a total of {count} licenses")
