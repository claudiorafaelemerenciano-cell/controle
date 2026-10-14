# Asterisk 20 LTS compilado do código-fonte com res_pjsip, app_audiosocket,
# AMI e func_curl. Compilar leva alguns minutos na primeira vez.
FROM debian:bookworm-slim AS build

ARG ASTERISK_VERSION=20-current
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates wget bzip2 patch pkg-config python3 xz-utils \
        libedit-dev libjansson-dev libxml2-dev uuid-dev libsqlite3-dev libssl-dev \
        libcurl4-openssl-dev libsrtp2-dev libncurses-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/asterisk
RUN wget -qO /tmp/asterisk.tar.gz \
        "https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz" \
    && tar xzf /tmp/asterisk.tar.gz --strip-components=1 \
    && rm /tmp/asterisk.tar.gz

RUN ./configure --with-pjproject-bundled --without-x11 --without-gtk2 \
    && make menuselect.makeopts \
    && menuselect/menuselect \
        --disable BUILD_NATIVE \
        --enable chan_pjsip --enable res_pjsip --enable res_pjsip_outbound_registration \
        --enable app_audiosocket --enable res_audiosocket --enable chan_audiosocket \
        --enable func_curl --enable res_curl --enable func_uri \
        --enable codec_ulaw --enable codec_alaw --enable codec_gsm \
        --enable app_dial --enable res_rtp_asterisk \
        --disable-category MENUSELECT_CORE_SOUNDS \
        --disable-category MENUSELECT_MOH \
        --disable-category MENUSELECT_EXTRA_SOUNDS \
        menuselect.makeopts \
    && make -j"$(nproc)" \
    && make install DESTDIR=/staging \
    && rm -rf /staging/usr/share/man

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates gettext-base libedit2 libjansson4 libxml2 libuuid1 libsqlite3-0 \
        libssl3 libcurl4 libsrtp2-1 libncurses6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /staging/ /
RUN ldconfig \
    && useradd --system --home /var/lib/asterisk asterisk \
    && mkdir -p /etc/asterisk/templates /var/run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk \
    && chown -R asterisk:asterisk /etc/asterisk /var/run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk

COPY conf/ /etc/asterisk/
COPY templates/ /etc/asterisk/templates/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# SIP 5060/udp, RTP 10000-10100/udp (ajustável por RTP_START/RTP_END). AMI fica só em 127.0.0.1.
EXPOSE 5060/udp 10000-10100/udp
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
