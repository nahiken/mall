package com.macro.mall.search.service.impl;

import cn.hutool.core.collection.ListUtil;
import cn.hutool.core.map.MapUtil;
import cn.hutool.core.util.StrUtil;
import co.elastic.clients.elasticsearch._types.aggregations.*;
import co.elastic.clients.elasticsearch._types.aggregations.Aggregation;
import co.elastic.clients.elasticsearch._types.query_dsl.*;
import co.elastic.clients.elasticsearch._types.query_dsl.QueryBuilders;
import co.elastic.clients.util.ObjectBuilder;
import com.macro.mall.search.dao.EsProductDao;
import com.macro.mall.search.domain.EsProduct;
import com.macro.mall.search.domain.EsProductRelatedInfo;
import com.macro.mall.search.repository.EsProductRepository;
import com.macro.mall.search.service.EsProductService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.*;
import org.springframework.data.elasticsearch.client.elc.*;
import org.springframework.data.elasticsearch.core.SearchHit;
import org.springframework.data.elasticsearch.core.SearchHits;
import org.springframework.stereotype.Service;
import org.springframework.util.CollectionUtils;

import java.util.*;
import java.util.function.Function;
import java.util.stream.Collectors;


/**
 * 搜索商品管理Service实现类
 * Created by macro on 2018/6/19.
 */
@Service
public class EsProductServiceImpl implements EsProductService {
    private static final Logger LOGGER = LoggerFactory.getLogger(EsProductServiceImpl.class);
    @Autowired
    private EsProductDao productDao;
    @Autowired
    private EsProductRepository productRepository;
    @Autowired
    private ElasticsearchTemplate elasticsearchTemplate;
    @Override
    public int importAll() {
        List<EsProduct> esProductList = productDao.getAllEsProductList(null);
        Iterable<EsProduct> esProductIterable = productRepository.saveAll(esProductList);
        Iterator<EsProduct> iterator = esProductIterable.iterator();
        int result = 0;
        while (iterator.hasNext()) {
            result++;
            iterator.next();
        }
        return result;
    }

    @Override
    public void delete(Long id) {
        productRepository.deleteById(id);
    }

    @Override
    public EsProduct create(Long id) {
        EsProduct result = null;
        List<EsProduct> esProductList = productDao.getAllEsProductList(id);
        if (esProductList.size() > 0) {
            EsProduct esProduct = esProductList.get(0);
            result = productRepository.save(esProduct);
        }
        return result;
    }

    @Override
    public void delete(List<Long> ids) {
        if (!CollectionUtils.isEmpty(ids)) {
            List<EsProduct> esProductList = new ArrayList<>();
            for (Long id : ids) {
                EsProduct esProduct = new EsProduct();
                esProduct.setId(id);
                esProductList.add(esProduct);
            }
            productRepository.deleteAll(esProductList);
        }
    }

    @Override
    public Page<EsProduct> search(String keyword, Integer pageNum, Integer pageSize) {
        Pageable pageable = PageRequest.of(pageNum, pageSize);
        return productRepository.findByNameOrSubTitleOrKeywords(keyword, keyword, keyword, pageable);
    }

    @Override
    public Page<EsProduct> search(String keyword, Long brandId, Long productCategoryId, Integer pageNum, Integer pageSize,Integer sort) {
        Pageable pageable = PageRequest.of(pageNum, pageSize);
        NativeQueryBuilder nativeQueryBuilder = new NativeQueryBuilder();
        //分页
        nativeQueryBuilder.withPageable(pageable);
        //过滤
        if (brandId != null || productCategoryId != null) {
            Query boolQuery = QueryBuilders.bool(builder -> {
                if (brandId != null) {
                    builder.must(QueryBuilders.term(b -> b.field("brandId").value(brandId)));
                }
                if (productCategoryId != null) {
                    builder.must(QueryBuilders.term(b -> b.field("productCategoryId").value(productCategoryId)));
                }
                return builder;
            });
            nativeQueryBuilder.withFilter(boolQuery);
        }
        //搜索：多字段加权 + 关键词扩展。比如“衣服”会扩展到“服装、T恤、外套、衬衫”等。
        if (StrUtil.isEmpty(keyword)) {
            nativeQueryBuilder.withQuery(QueryBuilders.matchAll(builder -> builder));
        } else {
            List<String> searchTerms = expandSearchTerms(keyword);
            List<FunctionScore> functionScoreList = new ArrayList<>();
            for (String term : searchTerms) {
                functionScoreList.add(new FunctionScore.Builder()
                        .filter(QueryBuilders.match(builder -> builder.field("name").query(term)))
                        .weight(term.equals(keyword) ? 12.0 : 8.0)
                        .build());
                functionScoreList.add(new FunctionScore.Builder()
                        .filter(QueryBuilders.match(builder -> builder.field("subTitle").query(term)))
                        .weight(term.equals(keyword) ? 6.0 : 4.0)
                        .build());
                functionScoreList.add(new FunctionScore.Builder()
                        .filter(QueryBuilders.match(builder -> builder.field("keywords").query(term)))
                        .weight(term.equals(keyword) ? 5.0 : 3.0)
                        .build());
                functionScoreList.add(new FunctionScore.Builder()
                        .filter(QueryBuilders.match(builder -> builder.field("productCategoryName").query(term)))
                        .weight(term.equals(keyword) ? 8.0 : 6.0)
                        .build());
                functionScoreList.add(new FunctionScore.Builder()
                        .filter(QueryBuilders.match(builder -> builder.field("brandName").query(term)))
                        .weight(term.equals(keyword) ? 4.0 : 2.0)
                        .build());
            }
            FunctionScoreQuery.Builder functionScoreQueryBuilder = QueryBuilders.functionScore()
                    .functions(functionScoreList)
                    .scoreMode(FunctionScoreMode.Sum)
                    .minScore(2.0);
            nativeQueryBuilder.withQuery(builder -> builder.functionScore(functionScoreQueryBuilder.build()));
        }
        //排序
        if(sort==1){
            //按新品从新到旧
            nativeQueryBuilder.withSort(Sort.by(Sort.Order.desc("id")));
        }else if(sort==2){
            //按销量从高到低
            nativeQueryBuilder.withSort(Sort.by(Sort.Order.desc("sale")));
        }else if(sort==3){
            //按价格从低到高
            nativeQueryBuilder.withSort(Sort.by(Sort.Order.asc("price")));
        }else if(sort==4){
            //按价格从高到低
            nativeQueryBuilder.withSort(Sort.by(Sort.Order.desc("price")));
        }
        //按相关度
        nativeQueryBuilder.withSort(Sort.by(Sort.Order.desc("_score")));
        NativeQuery nativeQuery = nativeQueryBuilder.build();
        LOGGER.info("DSL:{}", nativeQuery.getQuery().toString());
        SearchHits<EsProduct> searchHits = elasticsearchTemplate.search(nativeQuery, EsProduct.class);
        if(searchHits.getTotalHits()<=0){
            return new PageImpl<>(ListUtil.empty(),pageable,0);
        }
        List<EsProduct> searchProductList = searchHits.stream().map(SearchHit::getContent).collect(Collectors.toList());
        return new PageImpl<>(searchProductList,pageable,searchHits.getTotalHits());
    }

    @Override
    public Page<EsProduct> recommend(Long id, Integer pageNum, Integer pageSize) {
        Pageable pageable = PageRequest.of(pageNum, pageSize);
        List<EsProduct> esProductList = productDao.getAllEsProductList(id);
        if (esProductList.size() > 0) {
            EsProduct esProduct = esProductList.get(0);
            String keyword = esProduct.getName();
            Long brandId = esProduct.getBrandId();
            Long productCategoryId = esProduct.getProductCategoryId();
            //构建查询条件
            NativeQueryBuilder nativeQueryBuilder = new NativeQueryBuilder();
            //分页
            nativeQueryBuilder.withPageable(pageable);
            //用于过滤掉相同的商品
            nativeQueryBuilder.withFilter(QueryBuilders.bool(build -> build.mustNot(QueryBuilders.term(b->b.field("id").value(id)))));
            //根据商品标题、品牌、分类进行搜索
            List<FunctionScore> functionScoreList = new ArrayList<>();
            functionScoreList.add(new FunctionScore.Builder()
                    .filter(QueryBuilders.match(builder -> builder.field("name").query(keyword)))
                    .weight(8.0)
                    .build());
            functionScoreList.add(new FunctionScore.Builder()
                    .filter(QueryBuilders.match(builder -> builder.field("subTitle").query(keyword)))
                    .weight(2.0)
                    .build());
            functionScoreList.add(new FunctionScore.Builder()
                    .filter(QueryBuilders.match(builder -> builder.field("keywords").query(keyword)))
                    .weight(2.0)
                    .build());
            functionScoreList.add(new FunctionScore.Builder()
                    .filter(QueryBuilders.match(builder -> builder.field("brandId").query(brandId)))
                    .weight(5.0)
                    .build());
            functionScoreList.add(new FunctionScore.Builder()
                    .filter(QueryBuilders.match(builder -> builder.field("productCategoryId").query(productCategoryId)))
                    .weight(3.0)
                    .build());
            FunctionScoreQuery.Builder functionScoreQueryBuilder = QueryBuilders.functionScore()
                    .functions(functionScoreList)
                    .scoreMode(FunctionScoreMode.Sum)
                    .minScore(2.0);
            nativeQueryBuilder.withQuery(builder -> builder.functionScore(functionScoreQueryBuilder.build()));
            NativeQuery nativeQuery = nativeQueryBuilder.build();
            LOGGER.info("DSL:{}", nativeQuery.getQuery().toString());
            SearchHits<EsProduct> searchHits = elasticsearchTemplate.search(nativeQuery, EsProduct.class);
            if(searchHits.getTotalHits()<=0){
                return new PageImpl<>(ListUtil.empty(),pageable,0);
            }
            List<EsProduct> searchProductList = searchHits.stream().map(SearchHit::getContent).collect(Collectors.toList());
            return new PageImpl<>(searchProductList,pageable,searchHits.getTotalHits());
        }
        return new PageImpl<>(ListUtil.empty());
    }

    @Override
    public EsProductRelatedInfo searchRelatedInfo(String keyword) {
        NativeQueryBuilder nativeQueryBuilder = new NativeQueryBuilder();
        //搜索条件
        if(StrUtil.isEmpty(keyword)){
            nativeQueryBuilder.withQuery(QueryBuilders.matchAll(builder -> builder));
        }else{
            List<String> searchTerms = expandSearchTerms(keyword);
            nativeQueryBuilder.withQuery(QueryBuilders.bool(builder -> {
                for (String term : searchTerms) {
                    builder.should(QueryBuilders.multiMatch(b -> b.fields("name", "subTitle", "keywords", "productCategoryName", "brandName").query(term)));
                }
                return builder.minimumShouldMatch("1");
            }));
        }
        //聚合搜索品牌名称
        nativeQueryBuilder.withAggregation("brandNames",AggregationBuilders.terms(builder -> builder.field("brandName").size(10)));
        //聚合搜索分类名称
        nativeQueryBuilder.withAggregation("productCategoryNames",AggregationBuilders.terms(builder -> builder.field("productCategoryName").size(10)));
        //聚合搜索商品属性，去除type=0的属性
        Aggregation aggregation = new Aggregation.Builder().nested(builder -> builder.path("attrValueList"))
                .aggregations("productAttrs",new Aggregation.Builder()
                        .filter(b->b.term(a->a.field("attrValueList.type").value("1")))
                        .aggregations("attrIds",new Aggregation.Builder().terms(b->b.field("attrValueList.productAttributeId").size(10))
                                .aggregations("attrValues",new Aggregation.Builder().terms(b->b.field("attrValueList.value").size(10)).build())
                                .aggregations("attrNames",new Aggregation.Builder().terms(b->b.field("attrValueList.name").size(10)).build())
                                .build()).build()).build();
        nativeQueryBuilder.withAggregation("allAttrValues",aggregation);
        NativeQuery nativeQuery = nativeQueryBuilder.build();
        LOGGER.info("DSL:{}", nativeQueryBuilder.getQuery().toString());
        SearchHits<EsProduct> searchHits = elasticsearchTemplate.search(nativeQuery, EsProduct.class);
        return convertProductRelatedInfo(searchHits);
    }

    /**
     * 将用户自然搜索词扩展为商城常见类目词，提升“衣服 -> T恤/外套”等召回效果。
     * ES 仍然只返回真实商品，不会凭空生成结果。
     */
    private List<String> expandSearchTerms(String keyword) {
        List<String> terms = new ArrayList<>();
        addTerm(terms, keyword);
        String normalized = keyword == null ? "" : keyword.trim().toLowerCase();
        Map<String, List<String>> expansions = new LinkedHashMap<>();
        expansions.put("衣服", Arrays.asList("服装", "T恤", "短袖", "外套", "衬衫", "休闲裤", "牛仔裤"));
        expansions.put("服装", Arrays.asList("衣服", "T恤", "短袖", "外套", "衬衫", "休闲裤", "牛仔裤"));
        expansions.put("上衣", Arrays.asList("T恤", "短袖", "外套", "衬衫"));
        expansions.put("裤子", Arrays.asList("休闲裤", "牛仔裤"));
        expansions.put("手机", Arrays.asList("手机数码", "手机通讯", "iPhone", "小米", "华为"));
        expansions.put("苹果手机", Arrays.asList("iPhone", "苹果", "手机通讯"));
        expansions.put("电脑", Arrays.asList("电脑办公", "笔记本", "平板电脑", "硬盘"));
        expansions.put("办公", Arrays.asList("电脑办公", "笔记本"));
        expansions.put("家电", Arrays.asList("家用电器", "电视", "空调", "洗衣机", "冰箱"));
        expansions.put("家具", Arrays.asList("家具家装", "卧室家具", "客厅家具"));
        expansions.put("家居", Arrays.asList("家具家装", "厨房卫浴", "灯饰照明"));
        expansions.put("车", Arrays.asList("汽车用品", "车载电器", "维修保养", "汽车装饰"));

        for (Map.Entry<String, List<String>> entry : expansions.entrySet()) {
            if (normalized.contains(entry.getKey().toLowerCase())) {
                for (String term : entry.getValue()) {
                    addTerm(terms, term);
                }
            }
        }
        return terms;
    }

    private void addTerm(List<String> terms, String term) {
        if (StrUtil.isEmpty(term)) {
            return;
        }
        String value = term.trim();
        if (!terms.contains(value)) {
            terms.add(value);
        }
    }
    /**
     * 将返回结果转换为对象
     */
    private EsProductRelatedInfo convertProductRelatedInfo(SearchHits<EsProduct> response) {
        EsProductRelatedInfo productRelatedInfo = new EsProductRelatedInfo();
        Map<String, ElasticsearchAggregation> esAggregationMap = ((ElasticsearchAggregations) response.getAggregations()).aggregationsAsMap();
        //设置品牌
        ElasticsearchAggregation brandNames = esAggregationMap.get("brandNames");
        List<String> brandNameList = new ArrayList<>();
        List<StringTermsBucket> brandNameBuckets = ((StringTermsAggregate) brandNames.aggregation().getAggregate()._get()).buckets().array();
        for(int i = 0; i<brandNameBuckets.size(); i++){
            brandNameList.add(brandNameBuckets.get(i).key().stringValue());
        }
        productRelatedInfo.setBrandNames(brandNameList);
        //设置分类
        ElasticsearchAggregation productCategoryNames = esAggregationMap.get("productCategoryNames");
        List<String> productCategoryNameList = new ArrayList<>();
        List<StringTermsBucket> productCategoryNameBuckets = ((StringTermsAggregate) productCategoryNames.aggregation().getAggregate()._get()).buckets().array();
        for(int i = 0; i<productCategoryNameBuckets.size(); i++){
            productCategoryNameList.add(productCategoryNameBuckets.get(i).key().stringValue());
        }
        productRelatedInfo.setProductCategoryNames(productCategoryNameList);
        //设置参数
        ElasticsearchAggregation productAttrs = esAggregationMap.get("allAttrValues");
        List<LongTermsBucket> attrIdBuckets = ((LongTermsAggregate) ((FilterAggregate) ((NestedAggregate) productAttrs.aggregation().getAggregate()._get()).aggregations().get("productAttrs")._get()).aggregations().get("attrIds")._get()).buckets().array();
        List<EsProductRelatedInfo.ProductAttr> attrList = new ArrayList<>();
        for (LongTermsBucket item : attrIdBuckets) {
            EsProductRelatedInfo.ProductAttr attr = new EsProductRelatedInfo.ProductAttr();
            attr.setAttrId(item.key());
            List<String> attrValueList = new ArrayList<>();
            List<StringTermsBucket> attrValues = ((StringTermsAggregate) item.aggregations().get("attrValues")._get()).buckets().array();
            List<StringTermsBucket> attrNames = ((StringTermsAggregate) item.aggregations().get("attrNames")._get()).buckets().array();
            for (StringTermsBucket attrValue : attrValues) {
                attrValueList.add(attrValue.key().stringValue());
            }
            attr.setAttrValues(attrValueList);
            if(!CollectionUtils.isEmpty(attrNames)){
                String attrName = attrNames.get(0).key().stringValue();
                attr.setAttrName(attrName);
            }
            attrList.add(attr);
        }
        productRelatedInfo.setProductAttrs(attrList);
        return productRelatedInfo;
    }
}
