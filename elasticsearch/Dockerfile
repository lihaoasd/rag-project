# Elasticsearch + IK 中文分词插件镜像
# 版本锁定：ES 镜像 tag 必须与 IK 插件 zip 的版本严格一致
FROM docker.elastic.co/elasticsearch/elasticsearch:8.13.4

# IK 插件（infinilabs/analysis-ik）。
# 注意：GitHub release 资产已清空，官方发布服务器 release.infinilabs.com 仍提供包
RUN bin/elasticsearch-plugin install --batch \
    https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.13.4.zip

# 远程词典配置：词库由后端导出，IK Monitor 轮询热更新
COPY config/IKAnalyzer.cfg.xml /usr/share/elasticsearch/config/analysis-ik/IKAnalyzer.cfg.xml
