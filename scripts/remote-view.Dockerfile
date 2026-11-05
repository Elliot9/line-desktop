# View the GTK client in a browser from macOS, Windows or any Docker host.
# The app runs on a virtual display inside the container; noVNC serves it.
#
#   docker build -t line-desktop-view -f scripts/remote-view.Dockerfile .
#   docker run --rm -p 127.0.0.1:6080:6080 line-desktop-view
#   open http://localhost:6080
#
# Add -e LINE_MODE=live to log in with a real account instead of the demo.

FROM golang:1.26.10-bookworm AS core
WORKDIR /src/core
COPY core/go.mod core/go.sum ./
RUN go mod download
COPY core/ ./
RUN arch=$(go env GOARCH) && CGO_ENABLED=0 GOTOOLCHAIN=local go build -mod=readonly -trimpath \
    -ldflags='-s -w' -o "/out/line-core-linux-$arch" .

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 libsecret-tools gnome-keyring \
      ca-certificates curl fonts-noto-cjk fonts-noto-color-emoji librsvg2-common util-linux \
      dbus dbus-x11 xvfb x11vnc x11-xserver-utils novnc websockify \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/line-view-web && ln -s /usr/share/novnc/* /opt/line-view-web/ \
    && printf '%s\n' '<!doctype html><meta charset="utf-8"><title>LINE GTK</title><meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale&reconnect=true&reconnect_delay=1500">' \
       > /opt/line-view-web/index.html
COPY . /opt/line-desktop
COPY --from=core /out/ /opt/line-desktop/bin/
RUN useradd --create-home line
USER line
ENV GSK_RENDERER=cairo GDK_BACKEND=x11 GTK_A11Y=none NO_AT_BRIDGE=1
EXPOSE 6080
CMD ["dbus-run-session", "--", "/opt/line-desktop/scripts/remote-view.sh"]
