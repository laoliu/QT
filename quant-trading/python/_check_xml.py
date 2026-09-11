import xml.etree.ElementTree as ET

tree = ET.parse(r"c:\temp\qt_test_results.xml")
root = tree.getroot()

failures = errors = passed = skipped = 0
no_msg = 0
for tc in root.iter("testcase"):
    f = tc.find("failure")
    e = tc.find("error")
    s = tc.find("skipped")
    if f is not None:
        failures += 1
        if not f.get("message"):
            no_msg += 1
            # Show text content
            if failures <= 3:
                print(f"FAILURE without message: text={f.text[:200] if f.text else 'None'}")
                print(f"  attribs: {dict(f.attrib)}")
    elif e is not None:
        errors += 1
    elif s is not None:
        skipped += 1
    else:
        passed += 1

print(f"\nfailures={failures}, errors={errors}, passed={passed}, skipped={skipped}")
print(f"failures without message: {no_msg}")
