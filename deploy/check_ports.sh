for port in 7860 7885 7886 7887; do
  echo -n "port $port: "
  curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:$port/
  echo
done