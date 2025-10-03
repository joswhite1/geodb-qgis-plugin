# def add_missing_fields(layer, fields):
#     # Get existing fields from the layer
#     existing_fields = {field.name(): field for field in layer.fields()}

#     # Compare and add missing fields
#     for field in fields:
#         if field.name() not in existing_fields:
#             layer.dataProvider().addAttributes([field])
#             print(f"Added field: {field.name()}")

#     # Update the layer's fields
#     layer.updateFields()



# def round_coordinates(geom, precision=6):
#     if geom.isMultipart():
#         parts = geom.asMultiPolygon()
#         rounded_parts = []
#         for part in parts:
#             rounded_part = []
#             for ring in part:
#                 rounded_ring = [QgsPointXY(round(pt.x(), precision), round(pt.y(), precision)) for pt in ring]
#                 rounded_part.append(rounded_ring)
#             rounded_parts.append(rounded_part)
#         return QgsGeometry.fromMultiPolygonXY(rounded_parts)
#     else:
#         part = geom.asPolygon()
#         rounded_part = []
#         for ring in part:
#             rounded_ring = [QgsPointXY(round(pt.x(), precision), round(pt.y(), precision)) for pt in ring]
#             rounded_part.append(rounded_ring)
#         return QgsGeometry.fromPolygonXY(rounded_part)













"""
    def save_landholding(self, data=None):
        # Path to your GeoPackage file
        q_project          = QgsProject.instance()
        geopackage_path    = q_project.readEntry("geodb_vars", "gpkg_filepath")[0]
        
        max_length_images = max(len(item.get('image_urls', [])) for item in data['serialized_data'])
        max_length_docs   = max(len(item.get('document_urls', [])) for item in data['serialized_data'])

        # Layer name
        layer_name = 'Landholding'
        new_layer = False
        # Check for existing layer already loaded into the project
        existing_layers = QgsProject.instance().mapLayersByName(layer_name)
        if existing_layers and existing_layers[0].source().split('|')[0] == geopackage_path:
            layer = existing_layers[0]
        else:  
            # if not, try to load the layer from the GeoPackage
            layer = QgsVectorLayer(f"{geopackage_path}|layername={layer_name}", layer_name, "ogr")

        if not layer.isValid():
            # Create a new layer
            fields = QgsFields()
            fields.append(QgsField('name', QMetaType.Type.QString))
            fields.append(QgsField('project', QMetaType.Type.QString))
            fields.append(QgsField('project_nk', QMetaType.Type.QString))
            fields.append(QgsField('claim_type', QMetaType.Type.QString))
            fields.append(QgsField('county', QMetaType.Type.QString))
            fields.append(QgsField('state', QMetaType.Type.QString))
            fields.append(QgsField('serial_number', QMetaType.Type.QString))
            fields.append(QgsField('serial_link', QMetaType.Type.QString))
            fields.append(QgsField('date_staked', QMetaType.Type.QDate))
            fields.append(QgsField('staked_by', QMetaType.Type.QString))
            fields.append(QgsField('notes', QMetaType.Type.QString))
            fields.append(QgsField('date_created', QMetaType.Type.QDate))
            fields.append(QgsField('created_by', QMetaType.Type.QString))
            for i in range(max_length_images):
                fields.append(QgsField(f'image_{i+1}', QMetaType.Type.QString))
            for i in range(max_length_docs):
                fields.append(QgsField(f'document_{i+1}', QMetaType.Type.QString))
            
            # create a new memory layer if we weren't able to load the layer from the GeoPackage
            layer = QgsVectorLayer(f"MultiPolygon?crs=EPSG:4326", layer_name, "memory")
            layer.dataProvider().addAttributes(fields)
            layer.updateFields()
            new_layer = True


        # Add the layer to the project if it's not already there
        if not QgsProject.instance().mapLayersByName(layer_name):
            QgsProject.instance().addMapLayer(layer)


        # Add features to the layer
        layer.startEditing()
        for item in data['serialized_data']:

            #create image links
            # names = item.get('get_images')
            urls     = item.get('image_urls')
            doc_urls = item.get('document_urls')

            attributes = [
                    item['name'],
                    item['project'],
                    json.dumps(item['project_nk']),
                    item['claim_type'],
                    item['county'],
                    item['state'],
                    item['serial_number'],
                    item['serial_link'],
                    item['date_staked'],
                    item['staked_by'],
                    item['notes'],
                    item['date_created'],
                    item['created_by'],
                ]
            # add column for each image and document
            for i in range(max_length_images):
                try:
                    attributes.append(f"{urls[i]}")
                except IndexError:
                    attributes.append('')

            for i in range(max_length_docs):
                try:
                    attributes.append(f"{doc_urls[i]}")
                except IndexError:
                    attributes.append('')

            # Check if feature exists
            existing_features = layer.getFeatures(f"name = '{item['name']}' AND project_nk = '{json.dumps(item['project_nk'])}'")
            existing_feature = next(existing_features, None)

            if existing_feature:
                print(f'Checking for updates: {item["name"]}')
                fid = existing_feature.id()
                attr_map = {}

                # Map field names to indices
                field_indices = {field.name(): idx for idx, field in enumerate(layer.fields())}

                for field_name, index in field_indices.items():
                        if field_name == 'fid':
                            #for existing features, we don't want to update the fid, and don't have it in the list of attributes
                            continue
                        existing_value = existing_feature[field_name]
                        # the attribute table does not have an fid entry at the beginning, so our indices are off by one
                        value          = attributes[index-1]

                        # Skip if comparing NULL and None
                        if (existing_value == NULL or existing_value is None) and value is None:
                            continue

                        # Special handling for project_nk field
                        if field_name == 'project_nk':
                            if json.dumps(value) != str(existing_value):
                                print(f'Updating {field_name}: {existing_value} -> {value}')
                                attr_map[field_indices[field_name]] = value
                        # Handle date fields
                        elif field_name in ['date_staked', 'date_created']:
                            if value:  # Only process if value exists
                                qdate = QDate.fromString(value, 'yyyy-MM-dd')
                                if existing_value != qdate:
                                    print(f'Updating {field_name}: {existing_value} -> {qdate}')
                                    attr_map[field_indices[field_name]] = qdate
                        else:
                            if str(existing_value) != str(value):
                                print(f'Updating {field_name}: {existing_value} -> {value}')
                                attr_map[field_indices[field_name]] = value

                # Update geometry if changed
                existing_geom = existing_feature.geometry().asWkt()
                if existing_geom != item['coords']:
                    print(f'Updating geometry')
                    existing_feature.setGeometry(QgsGeometry.fromWkt(item['coords']))
                    layer.changeGeometry(fid, existing_feature.geometry())

                # Apply attribute changes if any
                if attr_map:
                    layer.dataProvider().changeAttributeValues({fid: attr_map})

            else:
                # Add new feature
                print(f'Adding new feature: {item["name"]}')
                if new_layer is False:
                    attributes.insert(0, None)
                new_feature = QgsFeature()
                new_feature.setGeometry(QgsGeometry.fromWkt(item['coords']))
                new_feature.setAttributes(attributes) 
                layer.addFeature(new_feature)


        layer.commitChanges()
        layer.triggerRepaint()

        if layer.dataProvider().name() == "memory":
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = layer.name()

            _writer = QgsVectorFileWriter.writeAsVectorFormatV3(layer, geopackage_path, QgsCoordinateTransformContext(), options)

            # Update the layer's data source to point to the new GeoPackage
            layer.setDataSource(geopackage_path + f'|layername={layer.name()}', layer.name(), 'ogr')
    """