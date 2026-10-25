package com.macro.mall.search.controller;

import com.macro.mall.search.domain.EsProductRelatedInfo;
import com.macro.mall.search.service.EsProductService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * P2-01：P0-05 的写接口守卫回归测试。
 *
 * 这条守卫是「公网不能匿名重建/删除 ES 索引」的唯一控制器层保证：网关规则在
 * 换部署方式（直连 8081、内网反代）时会失效，控制器这层是最后一道。
 *
 * 纯单元测试：Mockito 假 service + ReflectionTestUtils 注入 @Value 字段，
 * 不起 Spring 上下文、不连 ES —— 所以能进 CI 常规流水线。
 */
class EsProductControllerSecurityTest {

    private static final String TOKEN = "unit-test-internal-token";

    private final EsProductService esProductService = mock(EsProductService.class);
    private final EsProductController controller = new EsProductController();

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(controller, "esProductService", esProductService);
    }

    @Test
    void writeApiDisabledWhenTokenNotConfigured() {
        setToken("");

        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> controller.importAllList(TOKEN));

        assertEquals(HttpStatus.FORBIDDEN, ex.getStatusCode());
        verifyNoInteractions(esProductService);
    }

    @Test
    void writeApiDisabledWhenTokenBlank() {
        setToken("   ");

        assertThrows(ResponseStatusException.class, () -> controller.importAllList(TOKEN));
        verifyNoInteractions(esProductService);
    }

    @Test
    void missingHeaderIsRejected() {
        setToken(TOKEN);

        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> controller.importAllList(null));

        assertEquals(HttpStatus.FORBIDDEN, ex.getStatusCode());
        verifyNoInteractions(esProductService);
    }

    @Test
    void wrongTokenIsRejected() {
        setToken(TOKEN);

        assertThrows(ResponseStatusException.class,
                () -> controller.importAllList(TOKEN + "-typo"));
        verifyNoInteractions(esProductService);
    }

    @Test
    void correctTokenAllowsImport() {
        setToken(TOKEN);
        when(esProductService.importAll()).thenReturn(7);

        assertEquals(7, controller.importAllList(TOKEN).getData());
        verify(esProductService).importAll();
    }

    @Test
    void correctTokenAllowsDeleteById() {
        setToken(TOKEN);

        controller.delete(9L, TOKEN);

        verify(esProductService).delete(9L);
    }

    @Test
    void correctTokenAllowsCreate() {
        setToken(TOKEN);

        controller.create(9L, TOKEN);

        verify(esProductService).create(9L);
    }

    @Test
    void readEndpointDoesNotRequireToken() {
        // 读接口保持匿名：换成内部 token 校验会打断 App/portal 的正常搜索
        when(esProductService.searchRelatedInfo("华为")).thenReturn(new EsProductRelatedInfo());

        controller.searchRelatedInfo("华为");

        verify(esProductService).searchRelatedInfo("华为");
    }

    private void setToken(String token) {
        ReflectionTestUtils.setField(controller, "internalToken", token);
    }
}
