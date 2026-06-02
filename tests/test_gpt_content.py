from dependencies.gpt import build_user_content


def test_no_images_returns_plain_string():
    assert build_user_content("hello", None) == "hello"
    assert build_user_content("hello", []) == "hello"


def test_with_images_returns_multimodal_array():
    content = build_user_content("describe", ["data:image/png;base64,AAAA"])
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }


def test_multiple_images_all_appended():
    imgs = ["data:image/png;base64,A", "data:image/jpeg;base64,B"]
    content = build_user_content("t", imgs)
    urls = [c["image_url"]["url"] for c in content if c["type"] == "image_url"]
    assert urls == imgs
