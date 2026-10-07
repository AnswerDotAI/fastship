import fastship.release as relmod


def test_write_npm_version_preserves_other_content(tmp_path):
    package = tmp_path/"package.json"
    before = '{\n  "name": "demo",\n  "version": "0.1.2",\n  "style": "demo-0.1.2.css"\n}\n'
    package.write_text(before)
    relmod._write_npm_version(package, "0.2.0")
    assert package.read_text() == before.replace('"version": "0.1.2"', '"version": "0.2.0"')
