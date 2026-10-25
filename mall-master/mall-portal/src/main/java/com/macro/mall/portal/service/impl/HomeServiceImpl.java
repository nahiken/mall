package com.macro.mall.portal.service.impl;

import com.github.pagehelper.PageHelper;
import com.macro.mall.mapper.*;
import com.macro.mall.model.*;
import com.macro.mall.portal.component.PortalMultiLevelCache;
import com.macro.mall.portal.dao.HomeDao;
import com.macro.mall.portal.domain.FlashPromotionProduct;
import com.macro.mall.portal.domain.HomeContentResult;
import com.macro.mall.portal.domain.HomeFlashPromotion;
import com.macro.mall.portal.service.HomeService;
import com.macro.mall.portal.util.DateUtil;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.util.CollectionUtils;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.function.Supplier;

/**
 * 首页内容管理Service实现类
 * Created by macro on 2019/1/28.
 */
@Service
public class HomeServiceImpl implements HomeService {
    @Autowired
    private PortalMultiLevelCache portalMultiLevelCache;

    /** 首页商品列表(page1)短 TTL：配置/推荐变更容忍 ~1 分钟收敛（#107） */
    private static final long HOME_LIST_TTL_SECONDS = 60;

    // ── P1-04：首页 content 分区缓存 ──────────────────────────────────────
    /** 广告/品牌/专题等低频配置：30~60s 内收敛（后台改动 ≤60s 可见） */
    private static final long HOME_CONTENT_TTL_SECONDS = 50;
    /** 秒杀场次/商品随时间窗变化，单独 ≤10s（L1 也随该 TTL 缩短，见 PortalMultiLevelCache） */
    private static final long HOME_FLASH_TTL_SECONDS = 10;
    /** P1-04 回滚开关：mall.cache.home=false 时每次全量查询 */
    @Value("${mall.cache.home:true}")
    private boolean homeCacheEnabled;
    @Autowired
    private SmsHomeAdvertiseMapper advertiseMapper;
    @Autowired
    private HomeDao homeDao;
    @Autowired
    private SmsFlashPromotionMapper flashPromotionMapper;
    @Autowired
    private SmsFlashPromotionSessionMapper promotionSessionMapper;
    @Autowired
    private PmsProductMapper productMapper;
    @Autowired
    private PmsProductCategoryMapper productCategoryMapper;
    @Autowired
    private CmsSubjectMapper subjectMapper;

    @Override
    public HomeContentResult content() {
        HomeContentResult result = new HomeContentResult();
        // P1-04：各区块分别缓存（不同时效），不再每次 6 查询。
        // 秒杀单独 10s：场次/商品随时间窗切换，不能沿用 50s。
        result.setAdvertiseList(cached("homeAdvertise", "type1",
                HOME_CONTENT_TTL_SECONDS, this::getHomeAdvertiseList));
        result.setBrandList(cached("homeBrand", "0:6",
                HOME_CONTENT_TTL_SECONDS, () -> homeDao.getRecommendBrandList(0, 6)));
        result.setHomeFlashPromotion(cached("homeFlash", "current",
                HOME_FLASH_TTL_SECONDS, this::getHomeFlashPromotion));
        // 新品/人气复用已有 page1 缓存（键与 newProductList/hotProductList 一致，避免重复查询）
        result.setNewProductList(newProductList(1, 4));
        result.setHotProductList(hotProductList(1, 4));
        result.setSubjectList(cached("homeSubject", "0:4",
                HOME_CONTENT_TTL_SECONDS, () -> homeDao.getRecommendSubjectList(0, 4)));
        return result;
    }

    /** 缓存开关统一入口：mall.cache.home=false 时直接回源。 */
    private <V> V cached(String cacheName, String key, long ttlSeconds, Supplier<V> loader) {
        if (!homeCacheEnabled) {
            return loader.get();
        }
        return portalMultiLevelCache.get(cacheName, key, ttlSeconds, loader);
    }

    @Override
    public List<PmsProduct> recommendProductList(Integer pageSize, Integer pageNum) {
        if (pageNum != null && pageNum == 1) {
            // #107：首页首屏固定形态走多级缓存（短 TTL 自然收敛）；翻页不缓存防串页
            String key = "pageSize:" + pageSize;
            return portalMultiLevelCache.get("homeRecommend", key, HOME_LIST_TTL_SECONDS, () -> queryRecommendProductList(pageSize, pageNum));
        }
        return queryRecommendProductList(pageSize, pageNum);
    }

    private List<PmsProduct> queryRecommendProductList(Integer pageSize, Integer pageNum) {
        // TODO: 2019/1/29 暂时默认推荐所有商品
        PageHelper.startPage(pageNum,pageSize);
        PmsProductExample example = new PmsProductExample();
        example.createCriteria()
                .andDeleteStatusEqualTo(0)
                .andPublishStatusEqualTo(1);
        List<PmsProduct> result = productMapper.selectByExample(example);
        // #107：selectByExample 返回 PageHelper.Page（ArrayList 子类），拷贝成纯 ArrayList，
        //       剥离 total/pageNum 等分页元数据，确保进 L1/L2 缓存的是纯 List（审查 P1）。
        return new ArrayList<>(result);
    }

    @Override
    public List<PmsProductCategory> getProductCateList(Long parentId) {
        PmsProductCategoryExample example = new PmsProductCategoryExample();
        example.createCriteria()
                .andShowStatusEqualTo(1)
                .andParentIdEqualTo(parentId);
        example.setOrderByClause("sort desc");
        return productCategoryMapper.selectByExample(example);
    }

    @Override
    public List<CmsSubject> getSubjectList(Long cateId, Integer pageSize, Integer pageNum) {
        PageHelper.startPage(pageNum,pageSize);
        CmsSubjectExample example = new CmsSubjectExample();
        CmsSubjectExample.Criteria criteria = example.createCriteria();
        criteria.andShowStatusEqualTo(1);
        if(cateId!=null){
            criteria.andCategoryIdEqualTo(cateId);
        }
        return subjectMapper.selectByExample(example);
    }

    @Override
    public List<PmsProduct> hotProductList(Integer pageNum, Integer pageSize) {
        if (pageNum != null && pageNum == 1) {
            // #107：首页首屏固定形态走多级缓存（短 TTL 自然收敛）；翻页不缓存防串页
            String key = "pageSize:" + pageSize;
            return portalMultiLevelCache.get("homeHot", key, HOME_LIST_TTL_SECONDS, () -> queryHotProductList(pageNum, pageSize));
        }
        return queryHotProductList(pageNum, pageSize);
    }

    private List<PmsProduct> queryHotProductList(Integer pageNum, Integer pageSize) {
        int offset = pageSize * (pageNum - 1);
        List<PmsProduct> result = homeDao.getHotProductList(offset, pageSize);
        // #107：统一拷贝成纯 ArrayList 再进缓存，杜绝把 Page 元数据/共享引用混入 L1/L2（审查 P1）
        return new ArrayList<>(result);
    }

    @Override
    public List<PmsProduct> newProductList(Integer pageNum, Integer pageSize) {
        if (pageNum != null && pageNum == 1) {
            // #107：首页首屏固定形态走多级缓存（短 TTL 自然收敛）；翻页不缓存防串页
            String key = "pageSize:" + pageSize;
            return portalMultiLevelCache.get("homeNew", key, HOME_LIST_TTL_SECONDS, () -> queryNewProductList(pageNum, pageSize));
        }
        return queryNewProductList(pageNum, pageSize);
    }

    private List<PmsProduct> queryNewProductList(Integer pageNum, Integer pageSize) {
        int offset = pageSize * (pageNum - 1);
        List<PmsProduct> result = homeDao.getNewProductList(offset, pageSize);
        // #107：统一拷贝成纯 ArrayList 再进缓存，杜绝把 Page 元数据/共享引用混入 L1/L2（审查 P1）
        return new ArrayList<>(result);
    }

    private HomeFlashPromotion getHomeFlashPromotion() {
        HomeFlashPromotion homeFlashPromotion = new HomeFlashPromotion();
        //获取当前秒杀活动
        Date now = new Date();
        SmsFlashPromotion flashPromotion = getFlashPromotion(now);
        if (flashPromotion != null) {
            //获取当前秒杀场次
            SmsFlashPromotionSession flashPromotionSession = getFlashPromotionSession(now);
            if (flashPromotionSession != null) {
                homeFlashPromotion.setStartTime(flashPromotionSession.getStartTime());
                homeFlashPromotion.setEndTime(flashPromotionSession.getEndTime());
                //获取下一个秒杀场次
                SmsFlashPromotionSession nextSession = getNextFlashPromotionSession(homeFlashPromotion.getStartTime());
                if(nextSession!=null){
                    homeFlashPromotion.setNextStartTime(nextSession.getStartTime());
                    homeFlashPromotion.setNextEndTime(nextSession.getEndTime());
                }
                //获取秒杀商品
                List<FlashPromotionProduct> flashProductList = homeDao.getFlashProductList(flashPromotion.getId(), flashPromotionSession.getId());
                homeFlashPromotion.setProductList(flashProductList);
            }
        }
        return homeFlashPromotion;
    }

    //获取下一个场次信息
    private SmsFlashPromotionSession getNextFlashPromotionSession(Date date) {
        SmsFlashPromotionSessionExample sessionExample = new SmsFlashPromotionSessionExample();
        sessionExample.createCriteria()
                .andStartTimeGreaterThan(date);
        sessionExample.setOrderByClause("start_time asc");
        List<SmsFlashPromotionSession> promotionSessionList = promotionSessionMapper.selectByExample(sessionExample);
        if (!CollectionUtils.isEmpty(promotionSessionList)) {
            return promotionSessionList.get(0);
        }
        return null;
    }

    private List<SmsHomeAdvertise> getHomeAdvertiseList() {
        SmsHomeAdvertiseExample example = new SmsHomeAdvertiseExample();
        example.createCriteria().andTypeEqualTo(1).andStatusEqualTo(1);
        example.setOrderByClause("sort desc");
        return advertiseMapper.selectByExample(example);
    }

    //根据时间获取秒杀活动
    private SmsFlashPromotion getFlashPromotion(Date date) {
        Date currDate = DateUtil.getDate(date);
        SmsFlashPromotionExample example = new SmsFlashPromotionExample();
        example.createCriteria()
                .andStatusEqualTo(1)
                .andStartDateLessThanOrEqualTo(currDate)
                .andEndDateGreaterThanOrEqualTo(currDate);
        List<SmsFlashPromotion> flashPromotionList = flashPromotionMapper.selectByExample(example);
        if (!CollectionUtils.isEmpty(flashPromotionList)) {
            return flashPromotionList.get(0);
        }
        return null;
    }

    //根据时间获取秒杀场次
    private SmsFlashPromotionSession getFlashPromotionSession(Date date) {
        Date currTime = DateUtil.getTime(date);
        SmsFlashPromotionSessionExample sessionExample = new SmsFlashPromotionSessionExample();
        sessionExample.createCriteria()
                .andStartTimeLessThanOrEqualTo(currTime)
                .andEndTimeGreaterThanOrEqualTo(currTime);
        List<SmsFlashPromotionSession> promotionSessionList = promotionSessionMapper.selectByExample(sessionExample);
        if (!CollectionUtils.isEmpty(promotionSessionList)) {
            return promotionSessionList.get(0);
        }
        return null;
    }
}
