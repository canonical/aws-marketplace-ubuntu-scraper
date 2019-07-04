import json
import re
import sys
import time

import boto3
import click

from collections import OrderedDict

from seleniumwire import webdriver

CANONICAL_OWNER = "099720109477"


def get_regions(account_id, username, password):
    driver = webdriver.Firefox()
    wait = webdriver.support.ui.WebDriverWait(driver, 10)
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
    return region_list


def get_ami_details(region_client, ami, quickstart_slot, ami_id):
    # Get the ami owner
    resp = region_client.describe_images(
        Owners=[CANONICAL_OWNER], Filters=[{"Name": "image-id", "Values": [ami_id]}]
    )
    resp_len = len(resp.get("Images", []))

    if resp_len:  # This is a Canonical AMI
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
        name = resp["Images"][0]["Name"]
        match = re.match(name_regex, name)
        if not match:
            raise Exception("Image name {} could not be parsed".format(name))
        attrs = match.groupdict()
        ami["quickstart_slot"] = quickstart_slot
        ami["ami_id"] = ami_id
        for key, value in attrs.items():
            ami[key] = value
        return ami
    else:
        return None


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
def main(iam_account_id, iam_username, iam_password):
    ubuntu_quickstart_entries = OrderedDict()
    region_dict_list = get_regions(iam_account_id, iam_username, iam_password)

    for region_dict in region_dict_list:
        region_identifier = region_dict["id"]

        region_session = boto3.Session(region_name=region_identifier)
        region_client = region_session.client("ec2")

        driver = webdriver.Firefox()
        wait = webdriver.support.ui.WebDriverWait(driver, 20)
        driver.get("https://{}.signin.aws.amazon.com/console".format(iam_account_id))
        username_element = driver.find_element_by_id("username")
        username_element.send_keys(iam_username)
        password_element = driver.find_element_by_id("password")
        password_element.send_keys(iam_password)
        driver.find_element_by_id("signin_button").click()

        wait.until(lambda driver: driver.find_element_by_id("nav-regionMenu"))
        driver.find_element_by_id("nav-regionMenu").click()
        # Are we on the correct region already?
        region_full_name = "{} ({})".format(
            region_dict["name"], region_dict["location"]
        )
        current_region_element = driver.find_element_by_class_name("current-region")
        if current_region_element.text != region_full_name:
            wait.until(
                lambda driver: driver.find_element_by_xpath(
                    '//a[@data-region-id="{}"]'.format(region_identifier)
                )
            )
            driver.find_element_by_xpath(
                '//a[@data-region-id="{}"]'.format(region_identifier)
            ).click()
        else:
            driver.find_element_by_id("nav-regionMenu").click()

        wait.until(lambda driver: driver.find_element_by_id("nav-servicesMenu"))
        driver.find_element_by_id("nav-servicesMenu").click()
        wait.until(
            lambda driver: driver.find_element_by_xpath('//li[@data-service-id="ec2"]')
        )
        driver.find_element_by_xpath('//li[@data-service-id="ec2"]/a').click()
        wait.until(
            lambda driver: driver.find_element_by_id("gwt-debug-createInstanceView")
        )
        driver.find_element_by_xpath("//*[contains(text(), 'Launch Instance')]").click()
        wait.until(
            lambda driver: driver.find_element_by_id("gwt-debug-tab-QUICKSTART_AMIS")
        )
        driver.find_element_by_id("gwt-debug-tab-QUICKSTART_AMIS").click()
        wait.until(
            lambda driver: driver.find_element_by_id("gwt-debug-tab-QUICKSTART_AMIS")
        )
        time.sleep(5)
        wait.until(lambda driver: driver.find_element_by_id("gwt-debug-paginatorLabel"))
        for request in list(driver.requests):
            if "call=getQuickstartList" in request.path and request.response:
                region_quickstart_entries = json.loads(request.response.body)

                ubuntu_quick_start_listings = []
                quickstart_slot = 0
                for ami in region_quickstart_entries["amiList"]:
                    quickstart_slot = quickstart_slot + 1
                    if ami["platform"] == "ubuntu":
                        if ami.get("imageId64", None):
                            canonical_amd64_ami = get_ami_details(
                                region_client,
                                ami.copy(),
                                quickstart_slot,
                                ami.get("imageId64"),
                            )
                            if canonical_amd64_ami:
                                ubuntu_quick_start_listings.append(canonical_amd64_ami)

                        if ami.get("imageIdArm64", None):
                            canonical_arm64_ami = get_ami_details(
                                region_client,
                                ami.copy(),
                                quickstart_slot,
                                ami.get("imageIdArm64"),
                            )
                            if canonical_arm64_ami:
                                ubuntu_quick_start_listings.append(canonical_arm64_ami)

                ubuntu_quickstart_entries[
                    region_identifier
                ] = ubuntu_quick_start_listings
                # We only need one list so we can break here
                break

        driver.delete_all_cookies()
        driver.close()
        driver.quit()

    for region in sorted(ubuntu_quickstart_entries.keys()):
        print(region)
        for ubuntu_quickstart_entry in ubuntu_quickstart_entries[region]:
            print(
                "{} {} {} {} (Slot: {} , Title: {}, Description: {})".format(
                    ubuntu_quickstart_entry["release_version"],
                    ubuntu_quickstart_entry["serial"],
                    ubuntu_quickstart_entry["arch"],
                    ubuntu_quickstart_entry["ami_id"],
                    ubuntu_quickstart_entry["quickstart_slot"],
                    ubuntu_quickstart_entry["title"],
                    ubuntu_quickstart_entry["description"],
                )
            )
        print()


if __name__ == "__main__":
    sys.exit(main())
