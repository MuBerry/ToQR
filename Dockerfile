# ToQR — 通用部署版
FROM python:3.12-slim

WORKDIR /app

# 纯标准库实现，无第三方依赖，无需 pip install
COPY server/ server/
COPY ui/ ui/

# 配置与运行时数据统一落 /data（挂卷持久化）
ENV TOQR_ETC_DIR=/data \
    TOQR_VAR_DIR=/data \
    PYTHONUNBUFFERED=1

# 运行端口由启动参数决定，默认 8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python server/app.py --check || exit 1

ENTRYPOINT ["python", "server/app.py"]
CMD ["--port", "8080"]
