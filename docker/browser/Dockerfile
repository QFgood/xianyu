FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:99 HOME=/config
COPY --chmod=644 nginx.conf /tmp/browser-nginx.conf
COPY --chmod=755 start.sh /usr/local/bin/start-browser
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium xvfb openbox x11vnc novnc websockify socat nginx-light apache2-utils \
    python3 ca-certificates fonts-noto-cjk tini util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --home-dir /config browser \
    && mkdir -p /config /tmp/nginx /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix \
    && mv /tmp/browser-nginx.conf /etc/nginx/browser.conf \
    && chown -R browser:browser /config /tmp/nginx
USER browser
VOLUME /config
EXPOSE 7900 9223
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9222/json/version', timeout=3)"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start-browser"]
