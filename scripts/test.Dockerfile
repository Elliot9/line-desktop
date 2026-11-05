ARG BASE_IMAGE=debian:bookworm-slim
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
    libsecret-tools gnome-keyring dbus-x11 ca-certificates curl \
    fonts-noto-cjk fonts-noto-color-emoji librsvg2-common xvfb xauth xdotool imagemagick \
    gcc libc6-dev git procps && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
ENV GSK_RENDERER=cairo GDK_BACKEND=x11

