package com.example.cart.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.List;
import java.util.Map;

@Service
public class CarServiceImpl implements ICarService{

    @Autowired
    private RestTemplate restTemplate;



    @Autowired
    private ItemClient itemClient;




    @Override
    public List<Map<String, Object>> getItemsByIds(List<Long> ids) {
//        ResponseEntity<List<Map<String, Object>>> responseEntity =  restTemplate.exchange(
//                "http://item-service/items?ids={ids}",
//                HttpMethod.GET,
//                null,
//                new ParameterizedTypeReference<List<Map<String, Object>>>(){},
//                ids);
//        return responseEntity.getBody();

        return itemClient.getItemsByIds(ids).getItems();
    }
}
