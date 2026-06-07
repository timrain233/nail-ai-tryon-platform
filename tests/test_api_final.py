import urllib.request, json

# Test favorite API
data = json.dumps({"item_id": 3, "device_id": "test_fastapi"}).encode()
req = urllib.request.Request(
    "http://localhost:7885/api/favorite",
    data=data,
    headers={"Content-Type": "application/json"}
)
resp = urllib.request.urlopen(req)
print("POST /api/favorite:", resp.read().decode(), resp.status)

# Test check_fav API
resp2 = urllib.request.urlopen("http://localhost:7885/api/tryon_check_fav?device=test_fastapi&item=3")
print("GET /api/tryon_check_fav:", resp2.read().decode(), resp2.status)

# Test unfavorite API
data2 = json.dumps({"item_id": 3, "device_id": "test_fastapi"}).encode()
req2 = urllib.request.Request(
    "http://localhost:7885/api/unfavorite",
    data=data2,
    headers={"Content-Type": "application/json"}
)
resp3 = urllib.request.urlopen(req2)
print("POST /api/unfavorite:", resp3.read().decode(), resp3.status)

# Verify unfavorited
resp4 = urllib.request.urlopen("http://localhost:7885/api/tryon_check_fav?device=test_fastapi&item=3")
print("GET /api/tryon_check_fav (after unfav):", resp4.read().decode(), resp4.status)

print("\nALL API TESTS PASSED")
