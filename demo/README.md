# ecloud demo app + POC docs (durable copy on the Mac)

Served by the `dc-demo` deployment (namespace `demo`) in both DC k8s clusters.
`/app` is mounted from the `dc-demo-app` ConfigMap (keys: `app.py`, `docs.html`).
The app serves `/docs` by reading `/app/docs.html`. Clients reach it at
http://10.80.15.50/docs (anycast), .51 (DC1), .52 (DC2).

## Update the served docs (both DCs)
Edit `docs.html`, then for each context (dc1, dc2) via the SOCKS tunnel (`kd1`/`kd2`):
```
kubectl -n demo create configmap dc-demo-app \
  --from-file=app.py=app-dcN.py --from-file=docs.html=docs.html \
  --dry-run=client -o yaml | kubectl -n demo replace -f -
kubectl -n demo rollout restart deploy/dc-demo
```
Use `replace`, not `apply`: docs.html (>256KB) overflows the apply last-applied annotation.
