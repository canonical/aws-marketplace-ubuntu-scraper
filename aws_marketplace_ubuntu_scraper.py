import json
import re
import subprocess
import sys
import time

import boto3
import click
import requests

from botocore.exceptions import ClientError as botocoreClientError
from bs4 import BeautifulSoup
from joblib import Parallel, delayed
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import TimeoutException as SeleniumTimeoutException
from seleniumwire import webdriver

CANONICAL_OWNER = "099720109477"
AWS_UBUNTU_PRO_OWNER_ALIAS = "aws-marketplace"
AWS_UBUNTU_DEEP_LEARNING_OWNER_ALIAS = "amazon"
CANONICAL_MARKETPLACE_PROFILE = "565feec9-3d43-413e-9760-c651546613f2"


def get_regions(account_id, username, password, headless, only_regions):
    # region_dict = {"name": "US East", "location": "N. Virginia", "id": "us-east-1" }
    # return [region_dict]
    # region_dict = {"name": "Asia Pacific", "location": "Seoul", "id": "ap-northeast-2"}
    # return [region_dict]
    # region_dict = {"name": "Europe", "location": "Ireland",
    #                "id": "eu-west-1"}
    # return [region_dict]
    driver_options = Options()
    driver_options.headless = headless
    driver = webdriver.Firefox(options=driver_options)
    wait = WebDriverWait(driver, 10)
    driver.get("https://{}.signin.aws.amazon.com/console".format(account_id))
    username_element = driver.find_element_by_id("username")
    username_element.send_keys(username)
    password_element = driver.find_element_by_id("password")
    password_element.send_keys(password)
    driver.find_element_by_id("signin_button").click()
    wait.until(lambda driver: driver.find_element_by_name("awsc-mezz-data"))
    region_list_element = driver.find_element_by_name("awsc-mezz-data")
    region_list_str = region_list_element.get_attribute("content")
    region_list = json.loads(region_list_str)["regions"]

    driver.delete_all_cookies()
    driver.close()
    driver.quit()
    if only_regions:
        return [reg for reg in region_list if reg['id'] in only_regions]
    return region_list


@click.command()
@click.option(
    "--iam-account-id",
    envvar="IAM_ACCOUNT_ID",
    required=True,
    help="IAM User account ID",
)
@click.option(
    "--iam-username", envvar="IAM_USERNAME", required=True, help="IAM username"
)
@click.option(
    "--iam-password", envvar="IAM_PASSWORD", required=True, help="IAM User account ID"
)
@click.option(
    "--headless/--no-headless",
    default=True,
    help="Use selenium in headless mode to avoid Firefox browser opening",
)
@click.option(
    "--parallel/--no-parallel", default=True, help="Query regions in parallel.",
)
@click.option(
    "--only-regions", multiple=True, default=[]
)
def quicklaunch(iam_account_id, iam_username, iam_password, headless, parallel, only_regions):
    region_dict_list = get_regions(iam_account_id, iam_username, iam_password, headless, only_regions)
    driver_options = Options()
    driver_options.headless = headless

    def scrape_quicklaunch_regions(region_dict):
        def get_ami_details(region_client, ami, quickstart_slot, ami_id):
            # Get the ami details
            resp = region_client.describe_images(
                Filters=[{"Name": "image-id", "Values": [ami_id]}],
            )
            resp_len = len(resp.get("Images", []))
            if resp_len:
                image = resp["Images"][0]
                image_owner = image.get("ImageOwnerAlias", image.get("OwnerId"))
                name_regex = None
                if image_owner == CANONICAL_OWNER:
                    image_owner = "Canonical"
                    # This is a Canonical AMI
                    name_regex = (
                        r"ubuntu/images(-(?P<imgtype_path>[\w-]+))?/"
                        r"((?P<virt_storage>\w+(-\w+)?)/)?"
                        r"ubuntu-(?P<suite>\w+)-"
                        r"((?P<release_version>\d\d\.\d\d)-)?"
                        r"((?P<upload_type>\w+)-)?"
                        r"(?P<arch>\w+)-server-"
                        r"(?P<serial>\d+(\.\d{1,2})?)"
                        r"(\-(?P<custom>\w+))?"
                    )
                elif image_owner == AWS_UBUNTU_PRO_OWNER_ALIAS:
                    # This is an AWS Ubuntu AMI - used for Ubuntu Pro listings
                    # trusty-ua-tools-20191128-d984c693-feaa-4be0-bc34-2099410bc9cc-ami-075ab031d5a3404c6.4
                    name_regex = (
                        r".*?"
                        r"(?P<serial>\d+(\.\d{1,2})?)"
                        r"-.*?-"
                        r"(?P<source_ami>ami-\w+).*?"
                    )
                elif image_owner == AWS_UBUNTU_DEEP_LEARNING_OWNER_ALIAS:
                    # This is an AWS Ubuntu AMI - used for
                    # Ubuntu Deep learning and SQL server listings
                    # trusty-ua-tools-20191128-d984c693-feaa-4be0-bc34-2099410bc9cc-ami-075ab031d5a3404c6.4
                    # ubuntu-xenial-16.04-amd64-server-20190212-SQL_2017_Standard-2019.04.02
                    name_regex = (
                        r"ubuntu-(?P<suite>\w+)-"
                        r"((?P<release_version>\d\d\.\d\d)-)?"
                        r"(?P<arch>\w+)-server-"
                        r"(?P<serial>\d+(\.\d{1,2})?)"
                        r"-.*?"
                    )
                if name_regex:
                    ami["quickstart_slot"] = quickstart_slot
                    ami["ami_id"] = ami_id
                    ami["owner"] = image_owner
                    name = image["Name"]
                    match = re.match(name_regex, name)
                    if match:
                        attrs = match.groupdict()
                        for key, value in attrs.items():
                            ami[key] = value
                    return ami
                else:
                    return None
            else:
                return None

        region_identifier = region_dict["id"]
        print("scraping {} ...".format(region_identifier))
        region_session = boto3.Session(region_name=region_identifier)
        region_client = region_session.client("ec2")
        ubuntu_quick_start_listings = []
        driver = webdriver.Firefox(options=driver_options)
        try:
            wait = WebDriverWait(driver, 20)
            driver.get(
                "https://{}.signin.aws.amazon.com/console?region={}".format(iam_account_id, region_identifier)
            )
            wait.until(lambda driver: driver.find_element_by_id("username"))
            username_element = driver.find_element_by_id("username")
            username_element.send_keys(iam_username)
            password_element = driver.find_element_by_id("password")
            password_element.send_keys(iam_password)
            driver.find_element_by_id("signin_button").click()

            wait.until(EC.element_to_be_clickable((By.ID, 'EC2_LAUNCH_WIZARD')))
            driver.find_element(By.ID, "EC2_LAUNCH_WIZARD").click()

            wait.until(
                lambda driver: driver.find_element_by_xpath(
                    '//iframe[@id="instance-lx-gwt-frame"]'
                )
            )
            dashboard_iframe = driver.find_element_by_xpath(
                '//iframe[@id="instance-lx-gwt-frame"]'
            )
            driver.switch_to.frame(dashboard_iframe)

            wait.until(
                lambda driver: driver.find_element_by_id(
                    "gwt-debug-tab-QUICKSTART_AMIS"
                )
            )
            driver.find_element_by_id("gwt-debug-tab-QUICKSTART_AMIS").click()
            wait.until(
                lambda driver: driver.find_element_by_id(
                    "gwt-debug-tab-QUICKSTART_AMIS"
                )
            )
            wait.until(
                lambda driver: driver.find_element_by_id("gwt-debug-paginatorLabel")
            )
            # wait until JSON request is complete loads.
            # 3 seconds seems to be enough for all regions
            print("{} - Querying quickstart list".format(region_identifier))
            time.sleep(3)
            for request in list(driver.requests):
                if "call=getQuickstartList" in request.path and request.response:
                    region_quickstart_entries = json.loads(request.response.body)
                    with open(
                        "{}-getQuickstartList.json".format(region_identifier), "w"
                    ) as outfile:
                        json.dump(region_quickstart_entries, outfile, indent=4)

                    quickstart_slot = 0
                    for ami in region_quickstart_entries["amiList"]:
                        quickstart_slot = quickstart_slot + 1
                        if ami["platform"] == "ubuntu":
                            if ami.get("imageId64", None):
                                print(
                                    "{} - Querying ami details for AMD64 AMI {}".format(
                                        region_identifier, ami.get("imageId64")
                                    )
                                )
                                canonical_amd64_ami = get_ami_details(
                                    region_client,
                                    ami.copy(),
                                    quickstart_slot,
                                    ami.get("imageId64"),
                                )
                                if canonical_amd64_ami:
                                    canonical_amd64_ami["listing_arch"] = "amd64"
                                    ubuntu_quick_start_listings.append(
                                        canonical_amd64_ami
                                    )

                            if ami.get("imageIdArm64", None):
                                print(
                                    "{} - Querying ami details for ARM64 AMI {}".format(
                                        region_identifier, ami.get("imageIdArm64")
                                    )
                                )
                                canonical_arm64_ami = get_ami_details(
                                    region_client,
                                    ami.copy(),
                                    quickstart_slot,
                                    ami.get("imageIdArm64"),
                                )
                                if canonical_arm64_ami:
                                    canonical_arm64_ami["listing_arch"] = "arm64"
                                    ubuntu_quick_start_listings.append(
                                        canonical_arm64_ami
                                    )

                    # We only need one list so we can break here
                    break
        except SeleniumTimeoutException as ste:
            print(
                "SeleniumTimeoutException encountered when querying region {} ".format(
                    region_identifier
                )
            )
            print(ste.msg)
        except botocoreClientError as bce:
            print(
                "botocoreClientError encountered when AMI for region {} ".format(
                    region_identifier
                )
            )
            print(bce)
        finally:
            driver.delete_all_cookies()
            driver.close()
            driver.quit()
        return (region_identifier, ubuntu_quick_start_listings)

    n_jobs = -1 if parallel else 1
    parallel_quickstart_entries = Parallel(n_jobs=n_jobs)(
        delayed(scrape_quicklaunch_regions)(region_dict)
        for region_dict in region_dict_list
    )

    sorted_parallel_quickstart_entries = sorted(
        parallel_quickstart_entries, key=lambda tup: tup[0]
    )

    with open("quickstart_entries.json", "w") as quickstart_entries_json:
        json.dump(sorted_parallel_quickstart_entries, quickstart_entries_json, indent=4)

    issues = {}

    for region, ubuntu_quickstart_entries in sorted_parallel_quickstart_entries:
        print(region)
        region_amis = []
        region_expected_listings = {
            "16.04": ["amd64", "arm64"],
            "18.04": ["amd64", "arm64"],
            "20.04": ["amd64", "arm64"],
        }
        for ubuntu_quickstart_entry in ubuntu_quickstart_entries:
            print(
                "{} {}\n\t{} {} {} {} {} \n\t\t(Slot: {} , Description: {})".format(
                    ubuntu_quickstart_entry.get("title", ""),
                    ubuntu_quickstart_entry.get("listing_arch", ""),
                    ubuntu_quickstart_entry.get("release_version", ""),
                    ubuntu_quickstart_entry.get("serial", ""),
                    ubuntu_quickstart_entry.get("arch", ""),
                    ubuntu_quickstart_entry.get("ami_id", ""),
                    ubuntu_quickstart_entry.get("owner", ""),
                    ubuntu_quickstart_entry.get("quickstart_slot", ""),
                    ubuntu_quickstart_entry.get("description", ""),
                )
            )
            if ubuntu_quickstart_entry.get("owner", "") == "Canonical":
                release_version = ubuntu_quickstart_entry.get("release_version", "")
                if (
                    ubuntu_quickstart_entry.get("arch", "")
                    in region_expected_listings[release_version]
                ):
                    region_expected_listings[release_version].remove(
                        ubuntu_quickstart_entry.get("arch", "")
                    )
                region_amis.append(ubuntu_quickstart_entry.get("ami_id", ""))

            if ubuntu_quickstart_entry.get(
                "owner", ""
            ) == "Canonical" and ubuntu_quickstart_entry.get(
                "arch", ""
            ) != ubuntu_quickstart_entry.get(
                "listing_arch", ""
            ):
                issues.setdefault(region, []).append(
                    "'{}' listing arch {} and AMI ({}) arch {} are not equal ".format(
                        ubuntu_quickstart_entry.get("title", ""),
                        ubuntu_quickstart_entry.get("listing_arch", ""),
                        ubuntu_quickstart_entry.get("ami_id", ""),
                        ubuntu_quickstart_entry.get("arch", ""),
                    )
                )
            if (
                ubuntu_quickstart_entry.get("owner", "") == "Canonical"
                and int(ubuntu_quickstart_entry.get("quickstart_slot", "")) > 10
            ):
                issues.setdefault(region, []).append(
                    "'{}' {} listing slot is greater than 10 - slot {}".format(
                        ubuntu_quickstart_entry.get("title", ""),
                        ubuntu_quickstart_entry.get("listing_arch", ""),
                        ubuntu_quickstart_entry.get("quickstart_slot", ""),
                    )
                )
            if (
                ubuntu_quickstart_entry.get("owner", "") == "Canonical"
                and region_amis.count(ubuntu_quickstart_entry.get("ami_id", "")) > 1
            ):
                issues.setdefault(region, []).append(
                    "'{}' {} listing AMI {} appears more than once  ".format(
                        ubuntu_quickstart_entry.get("title", ""),
                        ubuntu_quickstart_entry.get("listing_arch", ""),
                        ubuntu_quickstart_entry.get("ami_id", ""),
                    )
                )
        print()

        for release_version, arches in region_expected_listings.items():
            if len(arches) > 0:
                for arch in arches:
                    issues.setdefault(region, []).append(
                        "There are no listings for {} {}  ".format(
                            release_version, arch,
                        )
                    )
    for region, region_issues in issues.items():
        print(region)
        for region_issue in region_issues:
            print("\t* {}".format(region_issue))
        print()


@click.command()
def marketplace():
    public_profile_url_base = "https://aws.amazon.com/marketplace/seller-profile"
    public_profile_url = "{}?id={}".format(
        public_profile_url_base, CANONICAL_MARKETPLACE_PROFILE
    )
    response = requests.get(public_profile_url)
    page_content = response.content
    page_soup = BeautifulSoup(page_content, features="html.parser")
    page_link_elements = page_soup.select("div.pagination-bar ul.pagination li a")
    page_links = set()
    for page_link_element in page_link_elements:
        href = page_link_element.get("href", None)
        if href:
            page_links.add("{}{}".format(public_profile_url_base, href))

    def scrape_marketplace(marketplace_url):
        page_count = ""
        page_count_regex = r".*?page=(?P<page_count>\d?)"
        match = re.match(page_count_regex, marketplace_url)

        if match:
            attrs = match.groupdict()
            page_count = attrs.get("page_count", None)

        response = requests.get(marketplace_url)
        page_content = response.content
        page_soup = BeautifulSoup(page_content, features="html.parser")
        product_elements = page_soup.select(
            "div.vendor-products article.products div.col-xs-10"
        )
        products = []
        product_order = (int(page_count) * 10) - 10
        product_in_page_order = 0
        for product_element in product_elements:
            product_order = product_order + 1
            product_in_page_order = product_in_page_order + 1

            product_title_element = product_element.select_one("div.row h1")
            product_title = (
                product_title_element.get_text().strip()
                if product_title_element
                else ""
            )

            product_version_element = product_element.select_one(
                "ul.info li:nth-child(1)"
            )
            product_version = (
                product_version_element.get_text().strip()
                if product_version_element
                else ""
            )

            product_pricing_element = product_element.select_one("p.pricing span.price")
            product_pricing = (
                product_pricing_element.get_text().strip()
                if product_pricing_element
                else ""
            )

            product_info_element = product_element.select_one("p.delivery")
            product_info = (
                product_info_element.get_text().strip() if product_info_element else ""
            )

            product_description_element = product_element.select_one("p.description")
            product_description = (
                product_description_element.get_text().strip()
                if product_description_element
                else ""
            )

            # Get more detailed information on this listing
            marketplace_url_element = product_title_element.select_one("a")
            marketplace_url = marketplace_url_element.get("href")
            listing_response = requests.get(
                "https://aws.amazon.com{}".format(marketplace_url)
            )
            listing_page_content = listing_response.content
            listing_page_soup = BeautifulSoup(
                listing_page_content, features="html.parser"
            )
            fullfillment_options_element = listing_page_soup.select_one(
                "div.pdp-attributes div.fulfillment-options ul li:nth-child(1)"
            )
            fullfillment_options = (
                fullfillment_options_element.get_text().strip()
                if fullfillment_options_element
                else ""
            )

            release_version = ""
            serial = ""
            version_regex = (
                r".*?(?P<release_version>\d\d\.\d\d?)"
                r".*?(?P<serial>\d\d\d\d\d\d\d\d(\.\d{1,2})?).*?"
            )

            match = re.match(version_regex, product_version)

            if match:
                attrs = match.groupdict()
                release_version = attrs.get("release_version", None)
                serial = attrs.get("serial", None)

            product_unique_identifier = "{} ({}) - {}".format(
                product_title, fullfillment_options, serial
            )
            product = {
                "unique_identifier": product_unique_identifier,
                "version": product_version,
                "release_version": release_version,
                "title": product_title,
                "pricing": product_pricing,
                "info": product_info,
                "description": product_description,
                "product_in_page_order": product_in_page_order,
                "page_order": page_count,
                "product_order": product_order,
                "serial": serial,
                "marketplace_url": "https://aws.amazon.com{}".format(marketplace_url),
                "type": fullfillment_options,
            }
            products.append(product)
        return (page_count, products)

    parallel_products = Parallel(n_jobs=-1)(
        delayed(scrape_marketplace)(page_link) for page_link in page_links
    )
    sorted_parallel_products = sorted(parallel_products, key=lambda tup: tup[0])
    print("Public profile URL: {}".format(public_profile_url))
    for page, products_per_page in sorted_parallel_products:
        for product in products_per_page:
            print(
                "\n{}\n\t\t"
                "Release: {}\n\t\t"
                "Serial: {}\n\t\t"
                "Version: {}\n\t\t"
                "Type: {}\n\t\t"
                "Page: {} \n\t\t"
                "Slot: {} \n\t\t"
                "Title: {}\n\t\t"
                "Description: \n\t\t\t\t{}\n\t\t"
                "URL: {}\n\t\t".format(
                    product["unique_identifier"],
                    product["release_version"],
                    product["serial"],
                    product["version"],
                    product["type"],
                    product["page_order"],
                    product["product_order"],
                    product["title"],
                    product["description"].replace("\n", "\n\t\t\t\t"),
                    product["marketplace_url"],
                )
            )


def _streams_get_image(region, suite, arch):
    cmd = ['/snap/bin/simplestreams.sstream-query', '--max=1',
           'http://cloud-images.ubuntu.com/releases/streams/v1/com.ubuntu.cloud:released:aws.sjson',
           f'crsn={region}', f'version={suite}', f'arch={arch}', 'virt=hvm',
           'root_store=ssd', '--output-format=%(id)s']
    return subprocess.check_output(cmd, encoding='utf-8', universal_newlines=True)


@click.command(name='quicklaunch-report')
@click.option(
    '--scraper-data', type=click.File('r'), required=True,
    show_default=True, default='quickstart_entries.json'
)
@click.option('--needs-update-only/--no-needs-update-only',
              show_default=True, default=False)
def quicklaunch_report(scraper_data, needs_update_only):
    """
    Print a table with which shows if the quickstart entries are up-to-date.
    This is checked against streams.

    Returns 0 if everything is fine (no updates needed)

    Returns 2 if updates are needed

    All other return codes indicate a failure in the software
    """
    from prettytable import PrettyTable
    t = PrettyTable()
    t.field_names = ['Region', 'Release', 'Arch', 'Position', 'Quickstart AMI', 'Streams AMI', 'Needs update']
    data = json.loads(scraper_data.read())
    needs_any_update = False
    for region in sorted(data[0:1]):
        print(f'Checking region {region[0]} ...')
        for ami in region[1]:
            if ami['owner'] != 'Canonical':
                # skip Amazon owned images for now in the report
                continue
            if ami['listing_arch'] == 'amd64':
                ami_id = ami['imageId64']
            elif ami['listing_arch'] == 'arm64':
                ami_id = ami['imageIdArm64']
            else:
                raise Exception('Unknown architecture {}'.format(ami['arch']))
            streams_ami_id = _streams_get_image(region[0], ami['release_version'], ami['listing_arch'])
            needs_update = ami_id == streams_ami_id
            if True or needs_update:
                needs_any_update = True
            if not needs_update_only or needs_update:
                t.add_row([region[0], ami['release_version'], ami['listing_arch'],
                           ami['quickstart_slot'], ami_id, streams_ami_id, needs_update])
    if needs_any_update:
        print(t.get_string(sortby='Region', reversesort=True))
        click.echo("There are some updates needed")
        # do return 2 which can then be checked in automation if updates are needed
        sys.exit(2)
    else:
        click.echo('No updates needed')

@click.group()
def main():
    pass


main.add_command(quicklaunch)
main.add_command(marketplace)
main.add_command(quicklaunch_report)

if __name__ == "__main__":
    sys.exit(main())
