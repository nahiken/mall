package com.macro.mall.search.controller;

import com.macro.mall.common.api.CommonPage;
import com.macro.mall.common.api.CommonResult;
import com.macro.mall.search.domain.EsProduct;
import com.macro.mall.search.domain.EsProductRelatedInfo;
import com.macro.mall.search.service.EsProductService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.enums.ParameterIn;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

/**
 * 搜索商品管理Controller
 * Created by macro on 2018/6/19.
 *
 * P0-05：写接口（importAll / delete / create）不再匿名可用。
 * - 网关（mall-gateway）对 /search-api/esProduct/{importAll,delete/** ,create/**}
 *   一律 403，公网无入口；
 * - 控制器再校验 `X-Internal-Token` == `search.internal-token`
 *   （环境变量 SEARCH_INTERNAL_TOKEN）；未配置该值 = 写接口整体关闭（fail-closed），
 *   运维需直连 :8081 并携带该头。
 * 读接口（search/simple、search、recommend、search/relate）不受影响。
 */
@Controller
@Tag(name = "EsProductController",description = "搜索商品管理")
@RequestMapping("/esProduct")
public class EsProductController {
    private static final String INTERNAL_TOKEN_HEADER = "X-Internal-Token";

    @Autowired
    private EsProductService esProductService;

    @Value("${search.internal-token:}")
    private String internalToken;

    /**
     * 写接口统一守卫：未配置 token 直接关闭写能力；配置后要求请求头与之相等。
     * 使用 MessageDigest.isEqual 做常量时间比较，避免逐字节比较的时序侧信道。
     */
    private void requireInternalToken(String provided) {
        if (internalToken == null || internalToken.isBlank()) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN,
                    "search write API disabled: SEARCH_INTERNAL_TOKEN is not configured");
        }
        if (provided == null || !MessageDigest.isEqual(
                provided.getBytes(StandardCharsets.UTF_8),
                internalToken.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "invalid internal token");
        }
    }

    @Operation(summary = "导入所有数据库中商品到ES")
    @RequestMapping(value = "/importAll", method = RequestMethod.POST)
    @ResponseBody
    public CommonResult<Integer> importAllList(
            @RequestHeader(value = INTERNAL_TOKEN_HEADER, required = false) String token) {
        requireInternalToken(token);
        int count = esProductService.importAll();
        return CommonResult.success(count);
    }

    @Operation(summary = "根据id删除商品")
    @RequestMapping(value = "/delete/{id}", method = RequestMethod.DELETE)
    @ResponseBody
    public CommonResult<Object> delete(@PathVariable Long id,
                                       @RequestHeader(value = INTERNAL_TOKEN_HEADER, required = false) String token) {
        requireInternalToken(token);
        esProductService.delete(id);
        return CommonResult.success(null);
    }

    @Operation(summary = "根据id批量删除商品")
    @RequestMapping(value = "/delete/batch", method = RequestMethod.POST)
    @ResponseBody
    public CommonResult<Object> delete(@RequestParam("ids") List<Long> ids,
                                       @RequestHeader(value = INTERNAL_TOKEN_HEADER, required = false) String token) {
        requireInternalToken(token);
        esProductService.delete(ids);
        return CommonResult.success(null);
    }

    @Operation(summary = "根据id创建商品")
    @RequestMapping(value = "/create/{id}", method = RequestMethod.POST)
    @ResponseBody
    public CommonResult<EsProduct> create(@PathVariable Long id,
                                         @RequestHeader(value = INTERNAL_TOKEN_HEADER, required = false) String token) {
        requireInternalToken(token);
        EsProduct esProduct = esProductService.create(id);
        if (esProduct != null) {
            return CommonResult.success(esProduct);
        } else {
            return CommonResult.failed();
        }
    }

    @Operation(summary = "简单搜索")
    @RequestMapping(value = "/search/simple", method = RequestMethod.GET)
    @ResponseBody
    public CommonResult<CommonPage<EsProduct>> search(@RequestParam(required = false) String keyword,
                                                      @RequestParam(required = false, defaultValue = "0") Integer pageNum,
                                                      @RequestParam(required = false, defaultValue = "5") Integer pageSize) {
        Page<EsProduct> esProductPage = esProductService.search(keyword, pageNum, pageSize);
        return CommonResult.success(CommonPage.restPage(esProductPage));
    }

    @Operation(summary = "综合搜索、筛选、排序")
    @Parameter(name = "sort", description = "排序字段:0->按相关度；1->按新品；2->按销量；3->价格从低到高；4->价格从高到低", in = ParameterIn.QUERY, schema = @Schema(type = "integer",defaultValue = "0",allowableValues = {"0","1","2","3","4"}))
    @RequestMapping(value = "/search", method = RequestMethod.GET)
    @ResponseBody
    public CommonResult<CommonPage<EsProduct>> search(@RequestParam(required = false) String keyword,
                                                      @RequestParam(required = false) Long brandId,
                                                      @RequestParam(required = false) Long productCategoryId,
                                                      @RequestParam(required = false, defaultValue = "0") Integer pageNum,
                                                      @RequestParam(required = false, defaultValue = "5") Integer pageSize,
                                                      @RequestParam(required = false, defaultValue = "0") Integer sort) {
        Page<EsProduct> esProductPage = esProductService.search(keyword, brandId, productCategoryId, pageNum, pageSize, sort);
        return CommonResult.success(CommonPage.restPage(esProductPage));
    }

    @Operation(summary = "根据商品id推荐商品")
    @RequestMapping(value = "/recommend/{id}", method = RequestMethod.GET)
    @ResponseBody
    public CommonResult<CommonPage<EsProduct>> recommend(@PathVariable Long id,
                                                         @RequestParam(required = false, defaultValue = "0") Integer pageNum,
                                                         @RequestParam(required = false, defaultValue = "5") Integer pageSize) {
        Page<EsProduct> esProductPage = esProductService.recommend(id, pageNum, pageSize);
        return CommonResult.success(CommonPage.restPage(esProductPage));
    }

    @Operation(summary = "获取搜索的相关品牌、分类及筛选属性")
    @RequestMapping(value = "/search/relate", method = RequestMethod.GET)
    @ResponseBody
    public CommonResult<EsProductRelatedInfo> searchRelatedInfo(@RequestParam(required = false) String keyword) {
        EsProductRelatedInfo productRelatedInfo = esProductService.searchRelatedInfo(keyword);
        return CommonResult.success(productRelatedInfo);
    }
}
